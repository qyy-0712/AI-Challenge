"""
Go编译器工具

使用官方Go Docker镜像实现Go代码的语法检查、编译、执行和模块管理功能。
支持Go模块依赖管理和测试功能。
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


class GoCheckType(Enum):
    """Go检查类型"""
    SYNTAX = "syntax"        # 语法检查
    COMPILE = "compile"      # 编译检查
    EXECUTE = "execute"      # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查
    TEST = "test"           # 测试检查
    MOD_INIT = "mod_init"   # 模块初始化
    MOD_TIDY = "mod_tidy"   # 模块整理
    MOD_DOWNLOAD = "mod_download"  # 模块下载


@dataclass
class GoCheckResult:
    """Go检查结果"""
    success: bool
    check_type: GoCheckType
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output: str = ""
    execution_time: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    test_results: List[Dict[str, Any]] = field(default_factory=list)  # 测试结果
    go_version: str = "1.21"
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
            "test_results": self.test_results,
            "go_version": self.go_version,
            "container_id": self.container_id
        }


@dataclass
class GoExecutionOptions:
    """Go执行选项"""
    timeout: int = 30
    check_dependencies: bool = True
    download_dependencies: bool = True
    capture_output: bool = True
    working_directory: str = "/workspace"
    go_version: str = "1.21"
    memory_limit: str = "256m"
    cpu_limit: int = 50000
    module_name: Optional[str] = None  # Go模块名
    build_tags: List[str] = field(default_factory=list)  # 构建标签
    race_detector: bool = False  # 竞态检测
    verbose: bool = False  # 详细输出


class GoCompilerError(Exception):
    """Go编译器异常"""
    pass


class GoDependencyError(GoCompilerError):
    """Go依赖管理异常"""
    pass


class GoSyntaxError(GoCompilerError):
    """Go语法错误"""
    pass


class GoModuleError(GoCompilerError):
    """Go模块管理异常"""
    pass


class GoTestError(GoCompilerError):
    """Go测试异常"""
    pass


class GoCompiler:
    """Go编译器工具类"""
    
    # Go Docker镜像版本映射
    GO_IMAGES = {
        "1.16": "golang:1.16-alpine",
        "1.17": "golang:1.17-alpine",
        "1.18": "golang:1.18-alpine",
        "1.19": "golang:1.19-alpine",
        "1.20": "golang:1.20-alpine",
        "1.21": "golang:1.21-alpine",
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化Go编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("Go编译器初始化完成")
    
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
    
    def _get_go_image(self, version: str) -> str:
        """获取指定版本的Go Docker镜像"""
        return self.GO_IMAGES.get(version, "golang:1.21-alpine")
    
    def _parse_go_error(self, error_output: str) -> List[str]:
        """解析Go错误输出"""
        errors = []
        
        # Go错误模式
        patterns = [
            r"([^\s:]+):(\d+):\s*(\d+):\s*(.+)",  # 文件名:行号:列号: 错误消息
            r"([^\s:]+):(\d+):\s*(.+)",           # 文件名:行号: 错误消息
            r"(build|compile|run|test) error:\s*(.+)",  # 构建错误
            r"(undefined|cannot|invalid|expected|missing)\s+(.+)",  # 语法错误
            r"package\s+(\S+)\s+is not in\s+(.+)",  # 包错误
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
    
    def _parse_go_mod_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析go mod命令输出，返回(依赖信息, 错误信息)"""
        dependencies = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测依赖信息
            if line.startswith("go: downloading"):
                # 提取依赖信息
                match = re.search(r'go: downloading\s+(\S+)\s+(.+)', line)
                if match:
                    dependencies.append(f"{match.group(1)} {match.group(2)}")
            
            # 检测错误
            if line.startswith("go:"):
                errors.append(line)
        
        return dependencies, errors
    
    def _parse_go_test_output(self, output: str) -> List[Dict[str, Any]]:
        """解析go test输出，返回测试结果"""
        test_results = []
        
        # 测试结果模式
        test_run_pattern = r"=== RUN\s+(\S+)"
        test_pass_pattern = r"--- PASS:\s+(\S+)\s+\(([\d.]+)s\)"
        test_fail_pattern = r"--- FAIL:\s+(\S+)\s+\(([\d.]+)s\)"
        test_summary_pattern = r"(PASS|FAIL|ok)\s+(\S+)\s+([\d.]+)s"
        
        lines = output.split('\n')
        current_test = None
        
        for line in lines:
            line = line.strip()
            
            # 检测测试开始
            test_run_match = re.search(test_run_pattern, line)
            if test_run_match:
                current_test = {
                    "name": test_run_match.group(1),
                    "status": "running",
                    "duration": 0.0,
                    "output": []
                }
                continue
            
            # 检测测试通过
            test_pass_match = re.search(test_pass_pattern, line)
            if test_pass_match and current_test:
                current_test["status"] = "passed"
                current_test["duration"] = float(test_pass_match.group(2))
                test_results.append(current_test)
                current_test = None
                continue
            
            # 检测测试失败
            test_fail_match = re.search(test_fail_pattern, line)
            if test_fail_match and current_test:
                current_test["status"] = "failed"
                current_test["duration"] = float(test_fail_match.group(2))
                test_results.append(current_test)
                current_test = None
                continue
            
            # 检测测试摘要
            test_summary_match = re.search(test_summary_pattern, line)
            if test_summary_match:
                status = test_summary_match.group(1)
                if status in ["PASS", "ok"]:
                    # 所有测试通过
                    pass
                else:
                    # 有测试失败
                    pass
            
            # 收集测试输出
            if current_test and line:
                current_test["output"].append(line)
        
        return test_results
    
    def _is_go_module(self, code: str) -> bool:
        """检测代码是否包含Go模块声明"""
        return "module " in code or "go.mod" in code
    
    def _extract_go_version_from_code(self, code: str) -> str:
        """从Go代码中推断Go版本"""
        # 检查代码中的版本特性
        version_features = {
            "1.21": [r"for\s+range\s+\S+\s*\{", r"slices\.", r"maps\."],
            "1.20": [r"compare\.And", r"errors\.Join", r"any\s*\("],
            "1.19": [r"type\s+parameter", r"generics\s+parameter", r"\[\s*T\s+any\s*\]"],
            "1.18": [r"\[\s*T\s+any\s*\]", r"type\s+T\s+interface\s*\{", r"~\s*\w+"],
            "1.17": [r"embed\s+\.", "io/fs", "os/fs"],
            "1.16": [r"io/fs", "embed"],
        }
        
        for version, patterns in version_features.items():
            for pattern in patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    return version
        
        # 默认返回1.18，因为这是支持泛型的现代Go的基础版本
        return "1.18"
    
    def _suggest_fix_for_error(self, error_message: str) -> Optional[str]:
        """为常见Go错误提供建议修复方案"""
        error_patterns_suggestions = [
            (r"undefined:\s+(\w+)", "未定义的标识符，请检查变量名是否正确，或者是否缺少import语句"),
            (r"cannot use \S+ \(type \S+\) as type \S+", "类型不匹配，请检查变量类型是否正确"),
            (r"missing return statement", "缺少返回语句，请确保函数在所有路径都有返回值"),
            (r"not enough arguments", "参数数量不足，请检查函数调用时参数是否完整"),
            (r"too many arguments", "参数数量过多，请检查函数调用时参数是否正确"),
            (r"import cycle not allowed", "存在循环导入，请重新设计包结构以避免循环依赖"),
            (r"package \S+ is not in GOROOT", "包不在标准库中，请检查模块路径或使用go mod tidy"),
            (r"go: cannot find main module", "找不到主模块，请确保在包含go.mod文件的目录中运行命令"),
            (r"go.mod file not found", "找不到go.mod文件，请使用go mod init初始化模块"),
            (r"build constraints exclude all Go files", "构建约束排除了所有Go文件，请检查构建标签"),
            (r"redeclared in this block", "变量重复声明，请检查是否有重复的变量名"),
            (r"unreachable code", "存在不可达代码，请检查逻辑流程"),
            (r"syntax error", "语法错误，请检查代码语法是否正确"),
        ]
        
        for pattern, suggestion in error_patterns_suggestions:
            if re.search(pattern, error_message, re.IGNORECASE):
                return suggestion
        
        return None
    
    async def check_syntax(self, 
                          code: str, 
                          filename: str = "main.go",
                          options: Optional[GoExecutionOptions] = None) -> GoCheckResult:
        """
        检查Go代码语法
        
        Args:
            code: Go代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            GoCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or GoExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_go_image(options.go_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("go")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="go",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                file_path = f"{options.working_directory}/{file_id}"
                
                # 如果是模块代码，需要初始化模块
                if self._is_go_module(code) and options.module_name:
                    # 创建go.mod文件
                    go_mod_content = f"module {options.module_name}\n\ngo {options.go_version}\n"
                    async with self.file_manager.temporary_file_context(
                        go_mod_content.encode('utf-8'),
                        "go.mod"
                    ) as mod_file_id:
                        # 更新挂载
                        mounts = self.file_manager.setup_file_mounts([file_id, mod_file_id])
                        # 重新获取容器
                        container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                command = ["go", "tool", "compile", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_go_error(stderr.decode('utf-8')) if stderr else []
                
                return GoCheckResult(
                    success=success,
                    check_type=GoCheckType.SYNTAX,
                    errors=errors,
                    output=stdout.decode('utf-8') if stdout else "",
                    execution_time=execution_time,
                    go_version=options.go_version,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Go语法检查超时: {e}")
            return GoCheckResult(
                success=False,
                check_type=GoCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time,
                go_version=options.go_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Go语法检查失败: {e}")
            return GoCheckResult(
                success=False,
                check_type=GoCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time,
                go_version=options.go_version
            )
    
    async def compile_code(self, 
                          code: str, 
                          filename: str = "main.go",
                          options: Optional[GoExecutionOptions] = None) -> GoCheckResult:
        """
        编译Go代码
        
        Args:
            code: Go代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            GoCheckResult: 编译结果
        """
        start_time = time.time()
        options = options or GoExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_go_image(options.go_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("go")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="go",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建编译命令
                file_path = f"{options.working_directory}/{file_id}"
                output_path = f"{options.working_directory}/app"
                
                # 如果是模块代码，需要初始化模块
                if self._is_go_module(code) and options.module_name:
                    # 创建go.mod文件
                    go_mod_content = f"module {options.module_name}\n\ngo {options.go_version}\n"
                    async with self.file_manager.temporary_file_context(
                        go_mod_content.encode('utf-8'),
                        "go.mod"
                    ) as mod_file_id:
                        # 更新挂载
                        mounts = self.file_manager.setup_file_mounts([file_id, mod_file_id])
                        # 重新获取容器
                        container = self.container_manager.client.containers.get(container_id)
                
                # 构建编译命令
                command = ["go", "build", "-o", output_path, file_path]
                
                # 添加构建标签
                if options.build_tags:
                    for tag in options.build_tags:
                        command.insert(-1, f"-tags={tag}")
                
                # 添加竞态检测
                if options.race_detector:
                    command.insert(-1, "-race")
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_go_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                return GoCheckResult(
                    success=success,
                    check_type=GoCheckType.COMPILE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    go_version=options.go_version,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Go代码编译超时: {e}")
            return GoCheckResult(
                success=False,
                check_type=GoCheckType.COMPILE,
                errors=[f"代码编译超时: {e}"],
                execution_time=time.time() - start_time,
                go_version=options.go_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Go代码编译失败: {e}")
            return GoCheckResult(
                success=False,
                check_type=GoCheckType.COMPILE,
                errors=[f"代码编译失败: {e}"],
                execution_time=time.time() - start_time,
                go_version=options.go_version
            )
    
    async def execute_code(self, 
                          code: str, 
                          filename: str = "main.go",
                          options: Optional[GoExecutionOptions] = None) -> GoCheckResult:
        """
        执行Go代码
        
        Args:
            code: Go代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            GoCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or GoExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_go_image(options.go_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("go")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="go",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建执行命令
                file_path = f"{options.working_directory}/{file_id}"
                
                # 如果是模块代码，需要初始化模块
                if self._is_go_module(code) and options.module_name:
                    # 创建go.mod文件
                    go_mod_content = f"module {options.module_name}\n\ngo {options.go_version}\n"
                    async with self.file_manager.temporary_file_context(
                        go_mod_content.encode('utf-8'),
                        "go.mod"
                    ) as mod_file_id:
                        # 更新挂载
                        mounts = self.file_manager.setup_file_mounts([file_id, mod_file_id])
                        # 重新获取容器
                        container = self.container_manager.client.containers.get(container_id)
                
                # 构建执行命令
                command = ["go", "run", file_path]
                
                # 添加构建标签
                if options.build_tags:
                    for tag in options.build_tags:
                        command.insert(-1, f"-tags={tag}")
                
                # 添加竞态检测
                if options.race_detector:
                    command.insert(-1, "-race")
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_go_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                return GoCheckResult(
                    success=success,
                    check_type=GoCheckType.EXECUTE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    go_version=options.go_version,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Go代码执行超时: {e}")
            return GoCheckResult(
                success=False,
                check_type=GoCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time,
                go_version=options.go_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Go代码执行失败: {e}")
            return GoCheckResult(
                success=False,
                check_type=GoCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time,
                go_version=options.go_version
            )

async def init_go_module(self,
                        module_name: str,
                        go_version: str = "1.21",
                        timeout: int = 30) -> GoCheckResult:
    """
    初始化Go模块
    
    Args:
        module_name: 模块名称
        go_version: Go版本
        timeout: 超时时间
        
    Returns:
        GoCheckResult: 初始化结果
    """
    start_time = time.time()
    
    # 确保清理任务已启动
    self._ensure_cleanup_tasks()
    
    try:
        # 设置容器配置
        image = self._get_go_image(go_version)
        resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
        
        # 获取或创建容器
        container_id = self.container_manager.get_container_for_language("go")
        if not container_id:
            container_id = self.container_manager.create_and_start_container(
                language="go",
                custom_image=image,
                custom_resource_limits=resource_limits
            )
        
        # 获取容器实例
        container = self.container_manager.client.containers.get(container_id)
        
        # 切换到工作目录并初始化模块
        command = ["go", "mod", "init", module_name]
        
        # 执行命令
        exit_code, stdout, stderr = self.container_manager._execute_command(
            container,
            command,
            timeout=timeout,
            workdir="/workspace"
        )
        
        execution_time = time.time() - start_time
        
        # 解析结果
        success = exit_code == 0
        output = stdout.decode('utf-8') if stdout else ""
        errors = self._parse_go_error(stderr.decode('utf-8')) if stderr else []
        
        return GoCheckResult(
            success=success,
            check_type=GoCheckType.MOD_INIT,
            errors=errors,
            output=output,
            execution_time=execution_time,
            go_version=go_version,
            container_id=container_id
        )
        
    except ContainerTimeoutError as e:
        logger.error(f"Go模块初始化超时: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.MOD_INIT,
            errors=[f"模块初始化超时: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )
    except (ContainerExecutionError, FileManagerError) as e:
        logger.error(f"Go模块初始化失败: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.MOD_INIT,
            errors=[f"模块初始化失败: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )

async def tidy_go_module(self,
                        go_version: str = "1.21",
                        timeout: int = 60) -> GoCheckResult:
    """
    整理Go模块依赖
    
    Args:
        go_version: Go版本
        timeout: 超时时间
        
    Returns:
        GoCheckResult: 整理结果
    """
    start_time = time.time()
    
    # 确保清理任务已启动
    self._ensure_cleanup_tasks()
    
    try:
        # 设置容器配置
        image = self._get_go_image(go_version)
        resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
        
        # 获取或创建容器
        container_id = self.container_manager.get_container_for_language("go")
        if not container_id:
            container_id = self.container_manager.create_and_start_container(
                language="go",
                custom_image=image,
                custom_resource_limits=resource_limits
            )
        
        # 获取容器实例
        container = self.container_manager.client.containers.get(container_id)
        
        # 整理模块依赖
        command = ["go", "mod", "tidy"]
        
        # 执行命令
        exit_code, stdout, stderr = self.container_manager._execute_command(
            container,
            command,
            timeout=timeout,
            workdir="/workspace"
        )
        
        execution_time = time.time() - start_time
        
        # 解析结果
        success = exit_code == 0
        output = stdout.decode('utf-8') if stdout else ""
        error_output = stderr.decode('utf-8') if stderr else ""
        
        # 解析依赖和错误
        dependencies, errors = self._parse_go_mod_output(output + error_output)
        
        return GoCheckResult(
            success=success,
            check_type=GoCheckType.MOD_TIDY,
            errors=errors,
            output=output,
            execution_time=execution_time,
            dependencies=dependencies,
            go_version=go_version,
            container_id=container_id
        )
        
    except ContainerTimeoutError as e:
        logger.error(f"Go模块整理超时: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.MOD_TIDY,
            errors=[f"模块整理超时: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )
    except (ContainerExecutionError, FileManagerError) as e:
        logger.error(f"Go模块整理失败: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.MOD_TIDY,
            errors=[f"模块整理失败: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )

async def download_go_module(self,
                            modules: List[str],
                            go_version: str = "1.21",
                            timeout: int = 120) -> GoCheckResult:
    """
    下载Go模块依赖
    
    Args:
        modules: 要下载的模块列表
        go_version: Go版本
        timeout: 超时时间
        
    Returns:
        GoCheckResult: 下载结果
    """
    start_time = time.time()
    
    # 确保清理任务已启动
    self._ensure_cleanup_tasks()
    
    try:
        # 设置容器配置
        image = self._get_go_image(go_version)
        resource_limits = ResourceLimits(memory="512m", cpu_quota=75000)
        
        # 获取或创建容器
        container_id = self.container_manager.get_container_for_language("go")
        if not container_id:
            container_id = self.container_manager.create_and_start_container(
                language="go",
                custom_image=image,
                custom_resource_limits=resource_limits
            )
        
        # 获取容器实例
        container = self.container_manager.client.containers.get(container_id)
        
        # 下载模块依赖
        command = ["go", "mod", "download"] + modules
        
        # 执行命令
        exit_code, stdout, stderr = self.container_manager._execute_command(
            container,
            command,
            timeout=timeout,
            workdir="/workspace"
        )
        
        execution_time = time.time() - start_time
        
        # 解析结果
        success = exit_code == 0
        output = stdout.decode('utf-8') if stdout else ""
        error_output = stderr.decode('utf-8') if stderr else ""
        
        # 解析依赖和错误
        dependencies, errors = self._parse_go_mod_output(output + error_output)
        
        return GoCheckResult(
            success=success,
            check_type=GoCheckType.MOD_DOWNLOAD,
            errors=errors,
            output=output,
            execution_time=execution_time,
            dependencies=dependencies,
            go_version=go_version,
            container_id=container_id
        )
        
    except ContainerTimeoutError as e:
        logger.error(f"Go模块下载超时: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.MOD_DOWNLOAD,
            errors=[f"模块下载超时: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )
    except (ContainerExecutionError, FileManagerError) as e:
        logger.error(f"Go模块下载失败: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.MOD_DOWNLOAD,
            errors=[f"模块下载失败: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )

async def run_go_tests(self,
                      test_path: str = "./...",
                      go_version: str = "1.21",
                      timeout: int = 60,
                      verbose: bool = False) -> GoCheckResult:
    """
    运行Go测试
    
    Args:
        test_path: 测试路径，默认为当前目录及子目录
        go_version: Go版本
        timeout: 超时时间
        verbose: 是否显示详细输出
        
    Returns:
        GoCheckResult: 测试结果
    """
    start_time = time.time()
    
    # 确保清理任务已启动
    self._ensure_cleanup_tasks()
    
    try:
        # 设置容器配置
        image = self._get_go_image(go_version)
        resource_limits = ResourceLimits(memory="512m", cpu_quota=75000)
        
        # 获取或创建容器
        container_id = self.container_manager.get_container_for_language("go")
        if not container_id:
            container_id = self.container_manager.create_and_start_container(
                language="go",
                custom_image=image,
                custom_resource_limits=resource_limits
            )
        
        # 获取容器实例
        container = self.container_manager.client.containers.get(container_id)
        
        # 构建测试命令
        command = ["go", "test", test_path]
        
        # 添加详细输出
        if verbose:
            command.append("-v")
        
        # 执行命令
        exit_code, stdout, stderr = self.container_manager._execute_command(
            container,
            command,
            timeout=timeout,
            workdir="/workspace"
        )
        
        execution_time = time.time() - start_time
        
        # 解析结果
        success = exit_code == 0
        output = stdout.decode('utf-8') if stdout else ""
        error_output = stderr.decode('utf-8') if stderr else ""
        
        # 解析测试结果
        test_results = self._parse_go_test_output(output)
        
        return GoCheckResult(
            success=success,
            check_type=GoCheckType.TEST,
            errors=[error_output] if error_output else [],
            output=output,
            execution_time=execution_time,
            test_results=test_results,
            go_version=go_version,
            container_id=container_id
        )
        
    except ContainerTimeoutError as e:
        logger.error(f"Go测试执行超时: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.TEST,
            errors=[f"测试执行超时: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )
    except (ContainerExecutionError, FileManagerError) as e:
        logger.error(f"Go测试执行失败: {e}")
        return GoCheckResult(
            success=False,
            check_type=GoCheckType.TEST,
            errors=[f"测试执行失败: {e}"],
            execution_time=time.time() - start_time,
            go_version=go_version
        )

async def enhanced_check_syntax(self,
                               code: str,
                               filename: str = "main.go",
                               options: Optional[GoExecutionOptions] = None) -> GoCheckResult:
    """
    增强的Go代码语法检查，包含错误分析和修复建议
    
    Args:
        code: Go代码
        filename: 文件名
        options: 执行选项
        
    Returns:
        GoCheckResult: 检查结果
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
    
    # 自动推断Go版本（如果未指定）
    if options and options.go_version:
        result.go_version = options.go_version
    else:
        inferred_version = self._extract_go_version_from_code(code)
        result.go_version = inferred_version
    
    return result