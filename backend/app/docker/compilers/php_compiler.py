"""
PHP编译器工具

使用官方PHP Docker镜像实现PHP代码的语法检查、执行和依赖管理功能。
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


class PHPCheckType(Enum):
    """PHP检查类型"""
    SYNTAX = "syntax"  # 语法检查
    LINT = "lint"      # 代码风格检查
    EXECUTE = "execute"  # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查


@dataclass
class PHPCheckResult:
    """PHP检查结果"""
    success: bool
    check_type: PHPCheckType
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
class PHPExecutionOptions:
    """PHP执行选项"""
    timeout: int = 30
    check_dependencies: bool = True
    install_dependencies: bool = True
    lint_code: bool = False
    capture_output: bool = True
    working_directory: str = "/workspace"
    php_version: str = "8.2"
    memory_limit: str = "256m"
    cpu_limit: int = 50000


class PHPCompilerError(Exception):
    """PHP编译器异常"""
    pass


class PHPDependencyError(PHPCompilerError):
    """PHP依赖管理异常"""
    pass


class PHPSyntaxError(PHPCompilerError):
    """PHP语法错误"""
    pass


class PHPCompiler:
    """PHP编译器工具类"""
    
    # PHP Docker镜像版本映射
    PHP_IMAGES = {
        "7.4": "php:7.4-cli",
        "8.0": "php:8.0-cli",
        "8.1": "php:8.1-cli",
        "8.2": "php:8.2-cli",
        "8.3": "php:8.3-cli",
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化PHP编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("PHP编译器初始化完成")
    
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
    
    def _get_php_image(self, version: str) -> str:
        """获取指定版本的PHP Docker镜像"""
        return self.PHP_IMAGES.get(version, "php:8.2-cli")
    
    def _parse_php_error(self, error_output: str) -> List[str]:
        """解析PHP错误输出"""
        errors = []
        
        # 常见PHP错误模式
        patterns = [
            r"Parse error:.*in\s+(.+?)\s+on line\s+(\d+)",  # 语法错误
            r"Fatal error:.*in\s+(.+?)\s+on line\s+(\d+)",  # 致命错误
            r"Warning:.*in\s+(.+?)\s+on line\s+(\d+)",     # 警告
            r"Notice:.*in\s+(.+?)\s+on line\s+(\d+)",      # 通知
            r"Error:\s+.+",                                 # 一般错误
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
    
    def _parse_composer_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析Composer输出，返回(已安装包, 错误信息)"""
        installed_packages = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测成功安装的包
            if "Generating autoload files" in line:
                # 找到依赖安装成功的标志
                continue
            
            # 检测包安装信息
            if " - Installing " in line or " - Updating " in line:
                # 使用简单的字符串分割提取包名
                parts = line.split()
                if len(parts) >= 3 and (parts[1] == "Installing" or parts[1] == "Updating"):
                    installed_packages.append(parts[2])
            
            # 检测错误
            if line.startswith("[ErrorException]") or line.startswith("[RuntimeException]"):
                errors.append(line)
        
        return installed_packages, errors
    
    async def check_syntax(self, 
                          code: str, 
                          filename: str = "script.php",
                          options: Optional[PHPExecutionOptions] = None) -> PHPCheckResult:
        """
        检查PHP代码语法
        
        Args:
            code: PHP代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            PHPCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or PHPExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_php_image(options.php_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("php")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="php",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                file_path = f"{options.working_directory}/{file_id}"
                command = ["php", "-l", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析错误
                errors = self._parse_php_error(error_output) if error_output else []
                
                return PHPCheckResult(
                    success=success,
                    check_type=PHPCheckType.SYNTAX,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"PHP语法检查超时: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"PHP语法检查失败: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def execute_code(self, 
                          code: str, 
                          filename: str = "script.php",
                          options: Optional[PHPExecutionOptions] = None) -> PHPCheckResult:
        """
        执行PHP代码
        
        Args:
            code: PHP代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            PHPCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or PHPExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_php_image(options.php_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("php")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="php",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建执行命令
                file_path = f"{options.working_directory}/{file_id}"
                command = ["php", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = stdout.decode('utf-8') if stdout else ""
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析错误
                errors = self._parse_php_error(error_output) if error_output else []
                
                return PHPCheckResult(
                    success=success,
                    check_type=PHPCheckType.EXECUTE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"PHP代码执行超时: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"PHP代码执行失败: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def install_dependencies(self, 
                                  composer_json: str,
                                  php_version: str = "8.2",
                                  timeout: int = 120) -> PHPCheckResult:
        """
        安装PHP依赖包
        
        Args:
            composer_json: composer.json文件内容
            php_version: PHP版本
            timeout: 超时时间
            
        Returns:
            PHPCheckResult: 安装结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 设置容器配置
            image = self._get_php_image(php_version)
            resource_limits = ResourceLimits(memory="512m", cpu_quota=75000)
            
            # 获取或创建容器
            container_id = self.container_manager.get_container_for_language("php")
            if not container_id:
                container_id = self.container_manager.create_and_start_container(
                    language="php",
                    custom_image=image,
                    custom_resource_limits=resource_limits
                )
            
            # 获取容器实例
            container = self.container_manager.client.containers.get(container_id)
            
            # 创建composer.json临时文件
            async with self.file_manager.temporary_file_context(
                composer_json.encode('utf-8'),
                "composer.json"
            ) as file_id:
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例并更新挂载
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建Composer安装命令
                command = ["sh", "-c", f"cd /workspace/{file_id} && composer install --no-interaction --prefer-dist"]
                
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
                installed_packages, errors = self._parse_composer_output(output + error_output)
                
                return PHPCheckResult(
                    success=success,
                    check_type=PHPCheckType.DEPENDENCIES,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=installed_packages,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"PHP依赖安装超时: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.DEPENDENCIES,
                errors=[f"依赖安装超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"PHP依赖安装失败: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.DEPENDENCIES,
                errors=[f"依赖安装失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def check_dependencies(self, 
                                composer_json: str,
                                php_version: str = "8.2") -> PHPCheckResult:
        """
        检查composer.json文件中的依赖
        
        Args:
            composer_json: composer.json文件内容
            php_version: PHP版本
            
        Returns:
            PHPCheckResult: 检查结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 解析composer.json文件
            try:
                composer_data = json.loads(composer_json)
            except json.JSONDecodeError as e:
                return PHPCheckResult(
                    success=False,
                    check_type=PHPCheckType.DEPENDENCIES,
                    errors=[f"composer.json格式错误: {e}"],
                    execution_time=time.time() - start_time
                )
            
            # 提取依赖信息
            require = composer_data.get("require", {})
            dev_require = composer_data.get("require-dev", {})
            
            # 检查PHP版本要求
            php_requirement = require.get("php")
            if php_requirement:
                # 这里可以添加更复杂的版本兼容性检查
                logger.info(f"检测到PHP版本要求: {php_requirement}")
            
            # 收集所有依赖
            all_dependencies = list(require.keys()) + list(dev_require.keys())
            if "php" in all_dependencies:
                all_dependencies.remove("php")  # 排除PHP本身
            
            execution_time = time.time() - start_time
            
            return PHPCheckResult(
                success=True,
                check_type=PHPCheckType.DEPENDENCIES,
                dependencies=all_dependencies,
                execution_time=execution_time,
                output=f"发现 {len(all_dependencies)} 个依赖包"
            )
            
        except Exception as e:
            logger.error(f"PHP依赖检查失败: {e}")
            return PHPCheckResult(
                success=False,
                check_type=PHPCheckType.DEPENDENCIES,
                errors=[f"依赖检查失败: {e}"],
                execution_time=time.time() - start_time
            )