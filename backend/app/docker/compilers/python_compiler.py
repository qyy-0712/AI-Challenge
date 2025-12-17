"""
Python编译器工具

使用官方Python Docker镜像实现Python代码的语法检查、执行和依赖管理功能。
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


class PythonCheckType(Enum):
    """Python检查类型"""
    SYNTAX = "syntax"  # 语法检查
    LINT = "lint"      # 代码风格检查
    EXECUTE = "execute"  # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查


@dataclass
class PythonCheckResult:
    """Python检查结果"""
    success: bool
    check_type: PythonCheckType
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output: str = ""
    execution_time: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    container_id: Optional[str] = None
    
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
            "container_id": self.container_id
        }


@dataclass
class PythonExecutionOptions:
    """Python执行选项"""
    timeout: int = 30
    check_dependencies: bool = True
    install_dependencies: bool = True
    lint_code: bool = False
    capture_output: bool = True
    working_directory: str = "/workspace"
    python_version: str = "3.11"
    memory_limit: str = "256m"
    cpu_limit: int = 50000


class PythonCompilerError(Exception):
    """Python编译器异常"""
    pass


class PythonDependencyError(PythonCompilerError):
    """Python依赖管理异常"""
    pass


class PythonSyntaxError(PythonCompilerError):
    """Python语法错误"""
    pass


class PythonCompiler:
    """Python编译器工具类"""
    
    # Python Docker镜像版本映射
    PYTHON_IMAGES = {
        "3.8": "python:3.8-slim",
        "3.9": "python:3.9-slim",
        "3.10": "python:3.10-slim",
        "3.11": "python:3.11-slim",
        "3.12": "python:3.12-slim",
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化Python编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("Python编译器初始化完成")
    
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
    
    def _get_python_image(self, version: str) -> str:
        """获取指定版本的Python Docker镜像"""
        return self.PYTHON_IMAGES.get(version, "python:3.11-slim")
    
    def _parse_python_error(self, error_output: str) -> List[str]:
        """解析Python错误输出"""
        errors = []
        
        # 常见Python错误模式
        patterns = [
            r"File \"([^\"]+)\", line (\d+)",  # 文件路径和行号
            r"(\w*Error|Exception):",          # 错误类型
            r"^\s*\^",                         # 错误位置指示符
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
    
    def _parse_pip_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析pip输出，返回(已安装包, 错误信息)"""
        installed_packages = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测成功安装的包
            if line.startswith("Successfully installed"):
                packages = line.replace("Successfully installed:", "").strip()
                installed_packages.extend(packages.split())
            
            # 检测错误
            if line.startswith("ERROR:") or line.startswith("WARNING:"):
                errors.append(line)
        
        return installed_packages, errors
    
    async def check_syntax(self, 
                          code: str, 
                          filename: str = "script.py",
                          options: Optional[PythonExecutionOptions] = None) -> PythonCheckResult:
        """
        检查Python代码语法
        
        Args:
            code: Python代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            PythonCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or PythonExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_python_image(options.python_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("python")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="python",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 更新容器挂载
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                file_path = f"{options.working_directory}/{file_id}"
                command = ["python", "-m", "py_compile", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_python_error(stderr.decode('utf-8')) if stderr else []
                
                return PythonCheckResult(
                    success=success,
                    check_type=PythonCheckType.SYNTAX,
                    errors=errors,
                    output=stdout.decode('utf-8') if stdout else "",
                    execution_time=execution_time,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Python语法检查超时: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Python语法检查失败: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def execute_code(self, 
                          code: str, 
                          filename: str = "script.py",
                          options: Optional[PythonExecutionOptions] = None) -> PythonCheckResult:
        """
        执行Python代码
        
        Args:
            code: Python代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            PythonCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or PythonExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_python_image(options.python_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("python")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="python",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建执行命令
                file_path = f"{options.working_directory}/{file_id}"
                command = ["python", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_python_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                return PythonCheckResult(
                    success=success,
                    check_type=PythonCheckType.EXECUTE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Python代码执行超时: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Python代码执行失败: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def install_dependencies(self, 
                                  requirements: List[str],
                                  python_version: str = "3.11",
                                  timeout: int = 120) -> PythonCheckResult:
        """
        安装Python依赖包
        
        Args:
            requirements: 依赖包列表
            python_version: Python版本
            timeout: 超时时间
            
        Returns:
            PythonCheckResult: 安装结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 设置容器配置
            image = self._get_python_image(python_version)
            resource_limits = ResourceLimits(memory="512m", cpu_quota=75000)
            
            # 获取或创建容器
            container_id = self.container_manager.get_container_for_language("python")
            if not container_id:
                container_id = self.container_manager.create_and_start_container(
                    language="python",
                    custom_image=image,
                    custom_resource_limits=resource_limits
                )
            
            # 获取容器实例
            container = self.container_manager.client.containers.get(container_id)
            
            # 构建pip安装命令
            command = ["pip", "install"] + requirements
            
            # 执行命令
            exit_code, stdout, stderr = self.container_manager._execute_command(
                container, 
                command, 
                timeout=timeout
            )
            
            execution_time = time.time() - start_time
            
            # 解析结果
            success = exit_code == 0
            output = stdout.decode('utf-8') if stdout else ""
            error_output = stderr.decode('utf-8') if stderr else ""
            
            # 解析安装的包和错误
            installed_packages, errors = self._parse_pip_output(output + error_output)
            
            return PythonCheckResult(
                success=success,
                check_type=PythonCheckType.DEPENDENCIES,
                errors=errors,
                output=output,
                execution_time=execution_time,
                dependencies=installed_packages,
                container_id=container_id
            )
            
        except ContainerTimeoutError as e:
            logger.error(f"Python依赖安装超时: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.DEPENDENCIES,
                errors=[f"依赖安装超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Python依赖安装失败: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.DEPENDENCIES,
                errors=[f"依赖安装失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def check_dependencies(self, 
                                requirements_file: str,
                                python_version: str = "3.11") -> PythonCheckResult:
        """
        检查requirements.txt文件中的依赖
        
        Args:
            requirements_file: requirements.txt文件内容
            python_version: Python版本
            
        Returns:
            PythonCheckResult: 检查结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                requirements_file.encode('utf-8'), 
                "requirements.txt"
            ) as file_id:
                
                # 设置容器配置
                image = self._get_python_image(python_version)
                resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("python")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="python",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建依赖检查命令
                file_path = f"/workspace/{file_id}"
                command = ["pip", "check"]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=30
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = stdout.decode('utf-8') if stdout else ""
                errors = [stderr.decode('utf-8')] if stderr else []
                
                # 提取依赖列表
                dependencies = []
                if success:
                    # 如果pip check成功，尝试解析requirements文件
                    for line in requirements_file.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            # 提取包名（忽略版本号）
                            package_name = re.split(r'[<>=!]', line)[0].strip()
                            if package_name:
                                dependencies.append(package_name)
                
                return PythonCheckResult(
                    success=success,
                    check_type=PythonCheckType.DEPENDENCIES,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Python依赖检查超时: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.DEPENDENCIES,
                errors=[f"依赖检查超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Python依赖检查失败: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.DEPENDENCIES,
                errors=[f"依赖检查失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def lint_code(self, 
                       code: str, 
                       filename: str = "script.py",
                       linter: str = "pyflakes",
                       python_version: str = "3.11") -> PythonCheckResult:
        """
        对Python代码进行代码风格检查
        
        Args:
            code: Python代码
            filename: 文件名
            linter: 代码检查工具 (pyflakes, pylint等)
            python_version: Python版本
            
        Returns:
            PythonCheckResult: 检查结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_python_image(python_version)
                resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("python")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="python",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 首先安装linter工具
                install_command = ["pip", "install", linter]
                try:
                    self.container_manager._execute_command(
                        container, 
                        install_command, 
                        timeout=60
                    )
                except ContainerExecutionError:
                    # 如果安装失败，继续尝试使用可能已安装的工具
                    pass
                
                # 构建代码检查命令
                file_path = f"/workspace/{file_id}"
                command = [linter, file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=30
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 将输出解析为警告
                warnings = []
                if output:
                    warnings.extend(line.strip() for line in output.split('\n') if line.strip())
                if error_output:
                    warnings.extend(line.strip() for line in error_output.split('\n') if line.strip())
                
                return PythonCheckResult(
                    success=success,
                    check_type=PythonCheckType.LINT,
                    warnings=warnings,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Python代码风格检查超时: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.LINT,
                errors=[f"代码风格检查超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Python代码风格检查失败: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.LINT,
                errors=[f"代码风格检查失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def comprehensive_check(self, 
                                 code: str, 
                                 filename: str = "script.py",
                                 requirements: Optional[List[str]] = None,
                                 requirements_file: Optional[str] = None,
                                 options: Optional[PythonExecutionOptions] = None) -> PythonCheckResult:
        """
        对Python代码进行综合检查
        
        Args:
            code: Python代码
            filename: 文件名
            requirements: 依赖包列表
            requirements_file: requirements.txt文件内容
            options: 执行选项
            
        Returns:
            PythonCheckResult: 综合检查结果
        """
        start_time = time.time()
        options = options or PythonExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        # 初始化结果
        result = PythonCheckResult(
            success=True,
            check_type=PythonCheckType.SYNTAX,  # 默认类型
            execution_time=0.0
        )
        
        try:
            # 1. 语法检查
            syntax_result = await self.check_syntax(code, filename, options)
            result.errors.extend(syntax_result.errors)
            result.execution_time += syntax_result.execution_time
            
            if not syntax_result.success:
                result.success = False
                return result
            
            # 2. 依赖检查和安装
            if options.check_dependencies and (requirements or requirements_file):
                if requirements:
                    # 安装指定依赖
                    install_result = await self.install_dependencies(
                        requirements, 
                        options.python_version
                    )
                    result.errors.extend(install_result.errors)
                    result.execution_time += install_result.execution_time
                    result.dependencies.extend(install_result.dependencies)
                    
                    if not install_result.success:
                        result.success = False
                
                elif requirements_file:
                    # 检查requirements文件
                    dep_result = await self.check_dependencies(
                        requirements_file, 
                        options.python_version
                    )
                    result.errors.extend(dep_result.errors)
                    result.execution_time += dep_result.execution_time
                    result.dependencies.extend(dep_result.dependencies)
                    
                    if not dep_result.success:
                        result.success = False
            
            # 3. 代码风格检查
            if options.lint_code:
                lint_result = await self.lint_code(code, filename, "pyflakes", options.python_version)
                result.warnings.extend(lint_result.warnings)
                result.errors.extend(lint_result.errors)
                result.execution_time += lint_result.execution_time
                
                # 代码风格检查失败不影响整体成功状态
                # 只记录警告和错误信息
            
            # 4. 代码执行（可选）
            if options.capture_output:
                exec_result = await self.execute_code(code, filename, options)
                result.output = exec_result.output
                result.errors.extend(exec_result.errors)
                result.execution_time += exec_result.execution_time
                
                if not exec_result.success:
                    result.success = False
            
            result.execution_time = time.time() - start_time
            return result
            
        except Exception as e:
            logger.error(f"Python综合检查失败: {e}")
            return PythonCheckResult(
                success=False,
                check_type=PythonCheckType.SYNTAX,
                errors=[f"综合检查失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def cleanup(self) -> None:
        """清理资源"""
        try:
            # 清理临时文件
            await self.file_manager.cleanup_temp_files()
            
            # 停止清理任务
            self.file_manager.stop_cleanup_task()
            self.container_manager._pool.stop_cleanup_task()
            
            logger.info("Python编译器资源清理完成")
        except Exception as e:
            logger.error(f"Python编译器资源清理失败: {e}")