"""
Java编译器工具

使用官方OpenJDK Docker镜像实现Java代码的语法检查、编译、执行和依赖管理功能。
支持Maven和Gradle构建工具操作。
"""

import asyncio
import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

from ..container_manager import (
    ContainerManager, 
    ContainerManagerError,
    ContainerExecutionError,
    ContainerTimeoutError,
    ResourceLimits
)
from ..file_manager import (
    FileManager, 
    FileManagerError,
    FileTransferError
)


# 配置日志
logger = logging.getLogger(__name__)


class JavaCheckType(Enum):
    """Java检查类型"""
    SYNTAX = "syntax"  # 语法检查
    COMPILE = "compile"  # 编译检查
    EXECUTE = "execute"  # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查
    MAVEN_BUILD = "maven_build"  # Maven构建
    GRADLE_BUILD = "gradle_build"  # Gradle构建


@dataclass
class JavaCheckResult:
    """Java检查结果"""
    success: bool
    check_type: JavaCheckType
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output: str = ""
    execution_time: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    class_files: List[str] = field(default_factory=list)  # 编译生成的类文件
    container_id: Optional[str] = None
    java_version: str = "17"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "success": self.success,
            "check_type": self.check_type.value,
            "errors": self.errors,
            "warnings": self.warnings,
            "output": self.output,
            "execution_time": self.execution_time,
            "dependencies": self.dependencies,
            "class_files": self.class_files,
            "container_id": self.container_id,
            "java_version": self.java_version
        }


@dataclass
class JavaExecutionOptions:
    """Java执行选项"""
    timeout: int = 60  # Java编译和执行需要更长的时间
    check_dependencies: bool = True
    install_dependencies: bool = True
    capture_output: bool = True
    working_directory: str = "/workspace"
    java_version: str = "17"
    memory_limit: str = "512m"  # Java需要更多内存
    cpu_limit: int = 100000  # Java需要更多CPU
    classpath: str = "."  # 类路径
    main_class: Optional[str] = None  # 主类名
    program_args: List[str] = field(default_factory=list)  # 程序参数
    build_tool: Optional[str] = None  # 构建工具: maven, gradle


class JavaCompilerError(Exception):
    """Java编译器异常"""
    pass


class JavaDependencyError(JavaCompilerError):
    """Java依赖管理异常"""
    pass


class JavaSyntaxError(JavaCompilerError):
    """Java语法错误"""
    pass


class JavaBuildError(JavaCompilerError):
    """Java构建错误"""
    pass


class JavaCompiler:
    """Java编译器工具类"""
    
    # Java Docker镜像版本映射
    JAVA_IMAGES = {
        "8": "openjdk:8-slim",
        "11": "openjdk:11-slim",
        "17": "openjdk:17-slim",
        "21": "openjdk:21-slim",
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化Java编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("Java编译器初始化完成")
    
    def _ensure_cleanup_tasks(self):
        """确保清理任务已启动"""
        if not self._cleanup_tasks_started:
            try:
                # 尝试获取当前事件循环
                loop = asyncio.get_running_loop()
                loop.create_task(self.file_manager.start_cleanup_task())
            except RuntimeError:
                # 没有运行中的事件循环，创建新任务将在下次调用时处理
                pass
            
            self.container_manager._pool.start_cleanup_task()
            self._cleanup_tasks_started = True
            logger.info("清理任务已启动")
    
    def _get_java_image(self, version: str) -> str:
        """获取指定版本的Java Docker镜像"""
        return self.JAVA_IMAGES.get(version, "openjdk:17-slim")
    
    def _parse_java_error(self, error_output: str) -> List[str]:
        """解析Java错误输出"""
        errors = []
        
        # Java错误模式
        patterns = [
            r"([A-Za-z0-9_]+\.java):(\d+):\s*error:\s*(.*)",  # 文件名:行号: error: 消息
            r"([A-Za-z0-9_]+\.java):(\d+):\s*warning:\s*(.*)",  # 文件名:行号: warning: 消息
            r"Exception in thread \"([^\"]+)\" (.+): (.+)",  # 异常信息
            r"^\s*at\s+([^\(]+)\(([^:]+):(\d+)\)",  # 堆栈跟踪
            r"([A-Za-z0-9_.$]+Exception):\s*(.+)",  # 异常类型和消息
        ]
        
        lines = error_output.split('\n')
        current_error = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # 检查是否是新的错误行
            is_error_start = any(re.search(pattern, line) for pattern in patterns)
            
            if is_error_start and current_error:
                # 保存上一个错误
                errors.append(' '.join(current_error))
                current_error = [line]
            else:
                current_error.append(line)
        
        # 添加最后一个错误
        if current_error:
            errors.append(' '.join(current_error))
        
        return errors
    
    def _parse_maven_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析Maven输出，返回(依赖信息, 错误信息)"""
        dependencies = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测依赖信息
            if "Downloading" in line and "/" in line:
                # 提取依赖信息
                match = re.search(r'Downloading: ([^\s]+)', line)
                if match:
                    dependencies.append(match.group(1))
            
            # 检测构建状态
            if "BUILD SUCCESS" in line:
                # 构建成功
                pass
            elif "BUILD FAILURE" in line:
                # 构建失败
                errors.append("Maven构建失败")
            
            # 检测错误
            if "[ERROR]" in line:
                errors.append(line.replace("[ERROR]", "").strip())
        
        return dependencies, errors
    
    def _parse_gradle_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析Gradle输出，返回(依赖信息, 错误信息)"""
        dependencies = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测依赖下载
            if line.startswith("Download ") and " from " in line:
                # 提取依赖信息
                match = re.search(r'Download ([^\s]+)', line)
                if match:
                    dependencies.append(match.group(1))
            
            # 检测构建状态
            if "BUILD SUCCESSFUL" in line:
                # 构建成功
                pass
            elif "BUILD FAILED" in line:
                # 构建失败
                errors.append("Gradle构建失败")
            
            # 检测错误
            if line.startswith(" FAILURE: "):
                errors.append(line.replace(" FAILURE: ", "").strip())
        
        return dependencies, errors
    
    def _is_project_structure(self, files: List[str]) -> Tuple[bool, Optional[str]]:
        """检测是否为项目结构，返回(是否为项目, 构建工具类型)"""
        has_pom = any(f.endswith("pom.xml") for f in files)
        has_gradle = any(f.endswith("build.gradle") or f.endswith("build.gradle.kts") for f in files)
        
        if has_pom:
            return True, "maven"
        elif has_gradle:
            return True, "gradle"
        
        return False, None
    
    def _extract_java_version_from_code(self, code: str) -> str:
        """从Java代码中推断Java版本"""
        # 检查代码中的版本特性
        version_features = {
            "21": [r"record\s+\w+", r"sealed\s+(class|interface)", r"pattern\s+matching"],
            "17": [r"sealed\s+(class|interface)", r"text\s+blocks"],
            "15": [r"text\s+blocks"],
            "14": [r"var\s+in", r"switch\s+expression"],
            "11": [r"var\s+", r"lambda", r"stream\(\)"],
            "8": [r"lambda", r"stream\(\)", r"::\w+"],
        }
        
        for version, patterns in version_features.items():
            for pattern in patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    return version
        
        # 默认返回11，因为这是现代Java开发的基础版本
        return "11"
    
    def _suggest_fix_for_error(self, error_message: str) -> Optional[str]:
        """为常见Java错误提供建议修复方案"""
        error_patterns_suggestions = [
            (r"package\s+\w+\s+does not exist", "缺少依赖包，请检查pom.xml或build.gradle中的依赖配置"),
            (r"cannot find symbol", "找不到符号，请检查变量或方法名是否正确，或者是否缺少import语句"),
            (r"method\s+\w+\s+is not defined", "方法未定义，请检查方法名是否正确，或者是否存在于类中"),
            (r"constructor\s+\w+\s+in class\s+\w+\s+cannot be applied", "构造函数参数不匹配，请检查参数类型和数量"),
            (r"incompatible types", "类型不兼容，请检查变量类型是否正确"),
            (r"java\.lang\.OutOfMemoryError", "内存不足，请尝试增加容器内存限制或优化代码"),
            (r"java\.lang\.StackOverflowError", "栈溢出，可能存在无限递归，请检查递归终止条件"),
            (r"ClassNotFoundException", "类未找到，请检查类名是否正确，或者是否在classpath中"),
            (r"NoClassDefFoundError", "类定义未找到，请检查类路径配置或依赖是否正确加载"),
            (r"java\.lang\.NullPointerException", "空指针异常，请检查变量是否为null"),
            (r"java\.lang\.IndexOutOfBoundsException", "索引越界，请检查数组或集合索引是否在有效范围内"),
            (r"Build failed", "构建失败，请检查项目配置文件和依赖关系"),
        ]
        
        for pattern, suggestion in error_patterns_suggestions:
            if re.search(pattern, error_message, re.IGNORECASE):
                return suggestion
        
        return None
    
    async def enhanced_check_syntax(self, 
                                   code: str, 
                                   filename: str = "Main.java",
                                   options: Optional[JavaExecutionOptions] = None) -> JavaCheckResult:
        """
        增强的Java代码语法检查，包含错误分析和修复建议
        
        Args:
            code: Java代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            JavaCheckResult: 检查结果
        """
        # 执行基本语法检查
        result = await self.check_syntax(code, filename, options)
        
        # 如果有错误，添加修复建议
        if not result.success and result.errors:
            enhanced_errors = []
            for error in result.errors:
                # 尝试获取修复建议
                suggestion = self._suggest_fix_for_error(error)
                if suggestion:
                    enhanced_errors.append(f"{error}\n建议: {suggestion}")
                else:
                    enhanced_errors.append(error)
            
            result.errors = enhanced_errors
        
        # 自动推断Java版本（如果未指定）
        if options and not options.java_version:
            inferred_version = self._extract_java_version_from_code(code)
            result.java_version = inferred_version
        
        return result
    
    async def check_syntax(self, 
                          code: str, 
                          filename: str = "Main.java",
                          options: Optional[JavaExecutionOptions] = None) -> JavaCheckResult:
        """
        检查Java代码语法
        
        Args:
            code: Java代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            JavaCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or JavaExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_java_image(options.java_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                file_path = f"{options.working_directory}/{file_id}"
                classpath = f"{options.working_directory}:{options.classpath}"
                command = ["javac", "-cp", classpath, file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_java_error(stderr.decode('utf-8')) if stderr else []
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.SYNTAX,
                    errors=errors,
                    output=stdout.decode('utf-8') if stdout else "",
                    execution_time=execution_time,
                    container_id=container_id,
                    java_version=options.java_version
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Java语法检查超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Java语法检查失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
    
    async def compile_code(self, 
                          code: str, 
                          filename: str = "Main.java",
                          options: Optional[JavaExecutionOptions] = None) -> JavaCheckResult:
        """
        编译Java代码
        
        Args:
            code: Java代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            JavaCheckResult: 编译结果
        """
        start_time = time.time()
        options = options or JavaExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_java_image(options.java_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建编译命令
                file_path = f"{options.working_directory}/{file_id}"
                classpath = f"{options.working_directory}:{options.classpath}"
                command = ["javac", "-cp", classpath, "-d", options.working_directory, file_path]
                
                # 执行编译命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_java_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                # 获取生成的类文件
                class_files = []
                if success:
                    # 获取文件名（不带扩展名）
                    base_name = Path(filename).stem
                    class_files.append(f"{base_name}.class")
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.COMPILE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    class_files=class_files,
                    container_id=container_id,
                    java_version=options.java_version
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Java代码编译超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.COMPILE,
                errors=[f"代码编译超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Java代码编译失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.COMPILE,
                errors=[f"代码编译失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
    
    async def execute_code(self, 
                          code: str, 
                          filename: str = "Main.java",
                          main_class: Optional[str] = None,
                          options: Optional[JavaExecutionOptions] = None) -> JavaCheckResult:
        """
        执行Java代码
        
        Args:
            code: Java代码
            filename: 文件名
            main_class: 主类名，如果不提供则从文件名推断
            options: 执行选项
            
        Returns:
            JavaCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or JavaExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_java_image(options.java_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 确定主类名
                if not main_class:
                    main_class = Path(filename).stem
                
                # 构建编译命令
                file_path = f"{options.working_directory}/{file_id}"
                classpath = f"{options.working_directory}:{options.classpath}"
                compile_command = ["javac", "-cp", classpath, "-d", options.working_directory, file_path]
                
                # 先编译代码
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    compile_command, 
                    timeout=options.timeout
                )
                
                # 如果编译失败，返回错误
                if exit_code != 0:
                    execution_time = time.time() - start_time
                    errors = self._parse_java_error(stderr.decode('utf-8')) if stderr else []
                    return JavaCheckResult(
                        success=False,
                        check_type=JavaCheckType.EXECUTE,
                        errors=errors,
                        output=stdout.decode('utf-8') if stdout else "",
                        execution_time=execution_time,
                        container_id=container_id,
                        java_version=options.java_version
                    )
                
                # 构建执行命令
                execute_command = ["java", "-cp", classpath, main_class] + options.program_args
                
                # 执行代码
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    execute_command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_java_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                # 获取生成的类文件
                class_files = [f"{main_class}.class"]
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.EXECUTE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    class_files=class_files,
                    container_id=container_id,
                    java_version=options.java_version
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Java代码执行超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Java代码执行失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
    
    async def check_dependencies(self, 
                                pom_file: str,
                                java_version: str = "17") -> JavaCheckResult:
        """
        检查pom.xml文件中的依赖
        
        Args:
            pom_file: pom.xml文件内容
            java_version: Java版本
            
        Returns:
            JavaCheckResult: 检查结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                pom_file.encode('utf-8'), 
                "pom.xml"
            ) as file_id:
                
                # 设置容器配置
                image = self._get_java_image(java_version)
                resource_limits = ResourceLimits(memory="512m", cpu_quota=75000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建Maven依赖检查命令
                command = ["mvn", "dependency:analyze", "-DoutputFile=/dev/stdout"]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=120
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析依赖信息
                dependencies, errors = self._parse_maven_output(output + error_output)
                success = exit_code == 0
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.DEPENDENCIES,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id,
                    java_version=java_version
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Java依赖检查超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.DEPENDENCIES,
                errors=[f"依赖检查超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Java依赖检查失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.DEPENDENCIES,
                errors=[f"依赖检查失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=java_version
            )
    
    async def install_maven_dependencies(self, 
                                       pom_file: str,
                                       java_version: str = "17",
                                       timeout: int = 300) -> JavaCheckResult:
        """
        安装Maven依赖
        
        Args:
            pom_file: pom.xml文件内容
            java_version: Java版本
            timeout: 超时时间
            
        Returns:
            JavaCheckResult: 安装结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                pom_file.encode('utf-8'), 
                "pom.xml"
            ) as file_id:
                
                # 设置容器配置
                image = self._get_java_image(java_version)
                resource_limits = ResourceLimits(memory="768m", cpu_quota=75000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建Maven依赖安装命令
                command = ["mvn", "dependency:resolve", "dependency:build-classpath"]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析安装的依赖包和错误
                dependencies, errors = self._parse_maven_output(output + error_output)
                success = exit_code == 0
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.DEPENDENCIES,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id,
                    java_version=java_version
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Maven依赖安装超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.DEPENDENCIES,
                errors=[f"依赖安装超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Maven依赖安装失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.DEPENDENCIES,
                errors=[f"依赖安装失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=java_version
            )
    
    async def maven_build(self, 
                         project_files: Dict[str, str],
                         goals: List[str] = None,
                         options: Optional[JavaExecutionOptions] = None) -> JavaCheckResult:
        """
        使用Maven构建项目
        
        Args:
            project_files: 项目文件字典 {文件名: 文件内容}
            goals: Maven目标列表，默认为["compile"]
            options: 执行选项
            
        Returns:
            JavaCheckResult: 构建结果
        """
        start_time = time.time()
        options = options or JavaExecutionOptions()
        goals = goals or ["compile"]
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 检查是否有pom.xml
            if "pom.xml" not in project_files:
                return JavaCheckResult(
                    success=False,
                    check_type=JavaCheckType.MAVEN_BUILD,
                    errors=["未找到pom.xml文件"],
                    execution_time=time.time() - start_time,
                    java_version=options.java_version
                )
            
            # 创建临时文件
            file_ids = []
            try:
                for filename, content in project_files.items():
                    file_id = self.file_manager.create_secure_temp_file(
                        content.encode('utf-8'), 
                        filename
                    )
                    file_ids.append(file_id)
                
                # 设置容器配置
                image = self._get_java_image(options.java_version)
                resource_limits = ResourceLimits(
                    memory="768m",  # Maven需要更多内存
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts(file_ids)
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建Maven命令
                maven_command = ["mvn"] + goals
                
                # 执行Maven命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    maven_command, 
                    timeout=options.timeout * 2  # Maven构建需要更长时间
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析Maven输出
                dependencies, errors = self._parse_maven_output(output + error_output)
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.MAVEN_BUILD,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id,
                    java_version=options.java_version
                )
                
            finally:
                # 清理临时文件
                if file_ids:
                    await self.file_manager.cleanup_temp_files(file_ids)
                
        except ContainerTimeoutError as e:
            logger.error(f"Maven构建超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.MAVEN_BUILD,
                errors=[f"Maven构建超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Maven构建失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.MAVEN_BUILD,
                errors=[f"Maven构建失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
    
    async def gradle_build(self, 
                          project_files: Dict[str, str],
                          tasks: List[str] = None,
                          options: Optional[JavaExecutionOptions] = None) -> JavaCheckResult:
        """
        使用Gradle构建项目
        
        Args:
            project_files: 项目文件字典 {文件名: 文件内容}
            tasks: Gradle任务列表，默认为["build"]
            options: 执行选项
            
        Returns:
            JavaCheckResult: 构建结果
        """
        start_time = time.time()
        options = options or JavaExecutionOptions()
        tasks = tasks or ["build"]
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 检查是否有build.gradle或build.gradle.kts
            has_build_file = any(
                f.endswith("build.gradle") or f.endswith("build.gradle.kts") 
                for f in project_files.keys()
            )
            
            if not has_build_file:
                return JavaCheckResult(
                    success=False,
                    check_type=JavaCheckType.GRADLE_BUILD,
                    errors=["未找到build.gradle或build.gradle.kts文件"],
                    execution_time=time.time() - start_time,
                    java_version=options.java_version
                )
            
            # 创建临时文件
            file_ids = []
            try:
                for filename, content in project_files.items():
                    file_id = self.file_manager.create_secure_temp_file(
                        content.encode('utf-8'), 
                        filename
                    )
                    file_ids.append(file_id)
                
                # 设置容器配置
                image = self._get_java_image(options.java_version)
                resource_limits = ResourceLimits(
                    memory="768m",  # Gradle需要更多内存
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("java")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="java",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts(file_ids)
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建Gradle命令
                gradle_command = ["./gradlew"] + tasks
                
                # 首先确保gradlew有执行权限
                chmod_command = ["chmod", "+x", "gradlew"]
                self.container_manager._execute_command(
                    container, 
                    chmod_command, 
                    timeout=10
                )
                
                # 执行Gradle命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    gradle_command, 
                    timeout=options.timeout * 2  # Gradle构建需要更长时间
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析Gradle输出
                dependencies, errors = self._parse_gradle_output(output + error_output)
                
                return JavaCheckResult(
                    success=success,
                    check_type=JavaCheckType.GRADLE_BUILD,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id,
                    java_version=options.java_version
                )
                
            finally:
                # 清理临时文件
                if file_ids:
                    await self.file_manager.cleanup_temp_files(file_ids)
                
        except ContainerTimeoutError as e:
            logger.error(f"Gradle构建超时: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.GRADLE_BUILD,
                errors=[f"Gradle构建超时: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Gradle构建失败: {e}")
            return JavaCheckResult(
                success=False,
                check_type=JavaCheckType.GRADLE_BUILD,
                errors=[f"Gradle构建失败: {e}"],
                execution_time=time.time() - start_time,
                java_version=options.java_version
            )