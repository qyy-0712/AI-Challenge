"""
JavaScript/TypeScript编译器工具

使用官方Node.js Docker镜像实现JavaScript/TypeScript代码的语法检查、执行和依赖管理功能。
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


class JavaScriptCheckType(Enum):
    """JavaScript/TypeScript检查类型"""
    SYNTAX = "syntax"  # 语法检查
    LINT = "lint"      # 代码风格检查
    EXECUTE = "execute"  # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查
    TYPE_CHECK = "type_check"  # TypeScript类型检查


@dataclass
class JavaScriptCheckResult:
    """JavaScript/TypeScript检查结果"""
    success: bool
    check_type: JavaScriptCheckType
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output: str = ""
    execution_time: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    container_id: Optional[str] = None
    language: str = "javascript"  # javascript 或 typescript
    
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
            "container_id": self.container_id,
            "language": self.language
        }


@dataclass
class JavaScriptExecutionOptions:
    """JavaScript/TypeScript执行选项"""
    timeout: int = 30
    check_dependencies: bool = True
    install_dependencies: bool = True
    lint_code: bool = False
    capture_output: bool = True
    working_directory: str = "/workspace"
    node_version: str = "18"
    typescript: bool = False
    memory_limit: str = "256m"
    cpu_limit: int = 50000
    package_manager: str = "npm"  # npm 或 yarn


class JavaScriptCompilerError(Exception):
    """JavaScript/TypeScript编译器异常"""
    pass


class JavaScriptDependencyError(JavaScriptCompilerError):
    """JavaScript/TypeScript依赖管理异常"""
    pass


class JavaScriptSyntaxError(JavaScriptCompilerError):
    """JavaScript/TypeScript语法错误"""
    pass


class JavaScriptTypeScriptError(JavaScriptCompilerError):
    """TypeScript类型检查错误"""
    pass


class JavaScriptCompiler:
    """JavaScript/TypeScript编译器工具类"""
    
    # Node.js Docker镜像版本映射
    NODE_IMAGES = {
        "16": "node:16-alpine",
        "18": "node:18-alpine",
        "20": "node:20-alpine",
        "21": "node:21-alpine",
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化JavaScript/TypeScript编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("JavaScript/TypeScript编译器初始化完成")
    
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
    
    def _get_node_image(self, version: str) -> str:
        """获取指定版本的Node.js Docker镜像"""
        return self.NODE_IMAGES.get(version, "node:18-alpine")
    
    def _detect_language(self, code: str, filename: str) -> str:
        """检测代码语言类型"""
        if filename.endswith('.ts') or filename.endswith('.tsx'):
            return "typescript"
        
        # 检查代码中的TypeScript特征
        ts_patterns = [
            r':\s*\w+\s*[=)\]]',  # 类型注解
            r'interface\s+\w+',    # 接口定义
            r'type\s+\w+\s*=',     # 类型别名
            r'enum\s+\w+',         # 枚举定义
            r'<\w+>',              # 泛型
            r'import\s+.*from\s+["\'][^"\']*\.ts',  # 导入TypeScript文件
        ]
        
        for pattern in ts_patterns:
            if re.search(pattern, code):
                return "typescript"
        
        return "javascript"
    
    def _parse_javascript_error(self, error_output: str) -> List[str]:
        """解析JavaScript/TypeScript错误输出"""
        errors = []
        
        # JavaScript/TypeScript错误模式
        patterns = [
            r"at\s+([^\s]+)\s+\(([^:]+):(\d+):(\d+)\)",  # 堆栈跟踪
            r"([^:]+):(\d+):(\d+)\s+([^:]+):(.*)",        # 文件路径和行号
            r"Error:\s+(.*)",                             # 错误消息
            r"TypeError:\s+(.*)",                          # 类型错误
            r"ReferenceError:\s+(.*)",                     # 引用错误
            r"SyntaxError:\s+(.*)",                        # 语法错误
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
    
    def _parse_npm_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析npm输出，返回(已安装包, 错误信息)"""
        installed_packages = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测成功安装的包
            if line.startswith("added") and "packages" in line:
                # npm 7+ 输出格式
                match = re.search(r'added (\d+) packages', line)
                if match:
                    # 这里无法获取具体包名，只返回数量信息
                    installed_packages.append(f"{match.group(1)} packages")
            elif "+ " in line:
                # 更详细的包信息
                package_info = line.replace("+ ", "").strip()
                installed_packages.append(package_info)
            
            # 检测错误
            if line.startswith("npm ERR!") or line.startswith("error"):
                errors.append(line)
        
        return installed_packages, errors
    
    def _parse_eslint_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析ESLint输出，返回(错误信息, 警告信息)"""
        errors = []
        warnings = []
        
        try:
            # 尝试解析JSON格式的ESLint输出
            if output.strip().startswith('[') or output.strip().startswith('{'):
                eslint_results = json.loads(output)
                
                if isinstance(eslint_results, list):
                    for result in eslint_results:
                        if result.get('messages'):
                            for message in result['messages']:
                                severity = message.get('severity', 1)
                                text = message.get('message', '')
                                line = message.get('line', '?')
                                column = message.get('column', '?')
                                
                                formatted_msg = f"Line {line}, Column {column}: {text}"
                                
                                if severity == 2:  # Error
                                    errors.append(formatted_msg)
                                elif severity == 1:  # Warning
                                    warnings.append(formatted_msg)
                else:
                    # 单个文件结果
                    if eslint_results.get('messages'):
                        for message in eslint_results['messages']:
                            severity = message.get('severity', 1)
                            text = message.get('message', '')
                            line = message.get('line', '?')
                            column = message.get('column', '?')
                            
                            formatted_msg = f"Line {line}, Column {column}: {text}"
                            
                            if severity == 2:  # Error
                                errors.append(formatted_msg)
                            elif severity == 1:  # Warning
                                warnings.append(formatted_msg)
            else:
                # 非JSON格式输出，按行解析
                lines = output.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and (line.lower().startswith('error') or 'error' in line.lower()):
                        errors.append(line)
                    elif line and (line.lower().startswith('warning') or 'warning' in line.lower()):
                        warnings.append(line)
        
        except json.JSONDecodeError:
            # 无法解析为JSON，按普通文本处理
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    if 'error' in line.lower():
                        errors.append(line)
                    elif 'warning' in line.lower():
                        warnings.append(line)
                    else:
                        errors.append(line)
        
        return errors, warnings
    
    def _is_typescript_file(self, filename: str) -> bool:
        """检查是否为TypeScript文件"""
        return filename.endswith(('.ts', '.tsx'))
    
    async def check_syntax(self,
                          code: str,
                          filename: str = "script.js",
                          node_version: str = "18") -> JavaScriptCheckResult:
        """
        检查JavaScript/TypeScript代码语法
        
        Args:
            code: JavaScript/TypeScript代码
            filename: 文件名
            node_version: Node.js版本
            
        Returns:
            JavaScriptCheckResult: 语法检查结果
        """
        start_time = time.time()
        language = self._detect_language(code, filename)
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_node_image(node_version)
                resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("javascript")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="javascript",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                file_path = f"/workspace/{file_id}"
                
                if language == "typescript":
                    # 对于TypeScript，使用tsc进行类型检查
                    # 首先检查TypeScript是否已安装
                    install_cmd = ["npm", "install", "-g", "typescript"]
                    self.container_manager._execute_command(container, install_cmd, timeout=60)
                    
                    # 执行TypeScript编译检查
                    command = ["npx", "tsc", "--noEmit", file_path]
                else:
                    # 对于JavaScript，使用node --check进行语法检查
                    command = ["node", "--check", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container,
                    command,
                    timeout=30
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_javascript_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                return JavaScriptCheckResult(
                    success=success,
                    check_type=JavaScriptCheckType.SYNTAX,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id,
                    language=language
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"JavaScript/TypeScript语法检查超时: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time,
                language=language
            )
    
    async def install_dependencies(self,
                                  dependencies: List[str],
                                  node_version: str = "18",
                                  package_manager: str = "npm",
                                  timeout: int = 120) -> JavaScriptCheckResult:
        """
        安装JavaScript/TypeScript依赖包
        
        Args:
            dependencies: 依赖包列表
            node_version: Node.js版本
            package_manager: 包管理器 (npm 或 yarn)
            timeout: 超时时间
            
        Returns:
            JavaScriptCheckResult: 安装结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 设置容器配置
            image = self._get_node_image(node_version)
            resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
            
            # 获取或创建容器
            container_id = self.container_manager.get_container_for_language("javascript")
            if not container_id:
                container_id = self.container_manager.create_and_start_container(
                    language="javascript",
                    custom_image=image,
                    custom_resource_limits=resource_limits
                )
            
            # 获取容器实例
            container = self.container_manager.client.containers.get(container_id)
            
            # 构建依赖安装命令
            if package_manager == "yarn":
                # 安装yarn
                install_yarn_cmd = ["npm", "install", "-g", "yarn"]
                self.container_manager._execute_command(container, install_yarn_cmd, timeout=60)
                
                # 使用yarn安装依赖
                command = ["yarn", "add"] + dependencies
            else:
                # 使用npm安装依赖
                command = ["npm", "install"] + dependencies
            
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
            
            # 解析安装的依赖包
            installed_packages, errors = self._parse_npm_output(output)
            if error_output:
                errors.extend(error_output.split('\n'))
            
            return JavaScriptCheckResult(
                success=success,
                check_type=JavaScriptCheckType.DEPENDENCIES,
                errors=errors,
                output=output,
                execution_time=execution_time,
                dependencies=installed_packages,
                container_id=container_id,
                language="javascript"
            )
                
        except ContainerTimeoutError as e:
            logger.error(f"JavaScript/TypeScript依赖安装超时: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.DEPENDENCIES,
                errors=[f"依赖安装超时: {e}"],
                execution_time=time.time() - start_time,
                language="javascript"
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"JavaScript/TypeScript依赖安装失败: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.DEPENDENCIES,
                errors=[f"依赖安装失败: {e}"],
                execution_time=time.time() - start_time,
                language="javascript"
            )
    
    async def check_dependencies(self,
                                package_json: str,
                                node_version: str = "18",
                                package_manager: str = "npm") -> JavaScriptCheckResult:
        """
        检查package.json中的依赖
        
        Args:
            package_json: package.json文件内容
            node_version: Node.js版本
            package_manager: 包管理器 (npm 或 yarn)
            
        Returns:
            JavaScriptCheckResult: 依赖检查结果
        """
        start_time = time.time()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时package.json文件
            async with self.file_manager.temporary_file_context(
                package_json.encode('utf-8'),
                "package.json"
            ) as file_id:
                
                # 设置容器配置
                image = self._get_node_image(node_version)
                resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("javascript")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="javascript",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建依赖检查命令
                if package_manager == "yarn":
                    # 安装yarn
                    install_yarn_cmd = ["npm", "install", "-g", "yarn"]
                    self.container_manager._execute_command(container, install_yarn_cmd, timeout=60)
                    
                    # 使用yarn检查依赖
                    command = ["yarn", "check"]
                else:
                    # 使用npm检查依赖
                    command = ["npm", "ls"]
                
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
                error_output = stderr.decode('utf-8') if stderr else []
                errors = [error_output] if error_output else []
                
                # 提取依赖列表
                dependencies = []
                if success:
                    try:
                        # 尝试解析package.json获取依赖列表
                        package_data = json.loads(package_json)
                        deps = package_data.get('dependencies', {})
                        dev_deps = package_data.get('devDependencies', {})
                        
                        dependencies = list(deps.keys()) + list(dev_deps.keys())
                    except json.JSONDecodeError:
                        errors.append("无法解析package.json文件")
                
                return JavaScriptCheckResult(
                    success=success,
                    check_type=JavaScriptCheckType.DEPENDENCIES,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id,
                    language="javascript"
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"JavaScript/TypeScript依赖检查超时: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.DEPENDENCIES,
                errors=[f"依赖检查超时: {e}"],
                execution_time=time.time() - start_time,
                language="javascript"
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"JavaScript/TypeScript依赖检查失败: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.DEPENDENCIES,
                errors=[f"依赖检查失败: {e}"],
                execution_time=time.time() - start_time,
                language="javascript"
            )
    
    async def lint_code(self,
                       code: str,
                       filename: str = "script.js",
                       linter: str = "eslint",
                       node_version: str = "18") -> JavaScriptCheckResult:
        """
        对JavaScript/TypeScript代码进行代码风格检查
        
        Args:
            code: JavaScript/TypeScript代码
            filename: 文件名
            linter: 代码检查工具 (eslint, jshint等)
            node_version: Node.js版本
            
        Returns:
            JavaScriptCheckResult: 检查结果
        """
        start_time = time.time()
        language = self._detect_language(code, filename)
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_node_image(node_version)
                resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("javascript")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="javascript",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 安装代码检查工具
                if linter == "eslint":
                    install_cmd = ["npm", "install", "-g", "eslint"]
                    self.container_manager._execute_command(container, install_cmd, timeout=60)
                    
                    # 如果是TypeScript，安装TypeScript解析器
                    if language == "typescript":
                        install_ts_cmd = ["npm", "install", "-g", "@typescript-eslint/parser", "@typescript-eslint/eslint-plugin"]
                        self.container_manager._execute_command(container, install_ts_cmd, timeout=60)
                
                # 构建代码检查命令
                file_path = f"/workspace/{file_id}"
                
                if linter == "eslint":
                    # 配置ESLint
                    if language == "typescript":
                        # 创建TypeScript ESLint配置
                        eslint_config = {
                            "parser": "@typescript-eslint/parser",
                            "plugins": ["@typescript-eslint"],
                            "extends": ["@typescript-eslint/recommended"],
                            "parserOptions": {
                                "ecmaVersion": 2018,
                                "sourceType": "module"
                            }
                        }
                        
                        config_file = ".eslintrc.json"
                        async with self.file_manager.temporary_file_context(
                            json.dumps(eslint_config).encode('utf-8'),
                            config_file
                        ) as config_id:
                            mounts = self.file_manager.setup_file_mounts([file_id, config_id])
                            command = ["eslint", file_path, "--format", "json"]
                    else:
                        # JavaScript ESLint配置
                        eslint_config = {
                            "env": {
                                "browser": True,
                                "es2021": True,
                                "node": True
                            },
                            "extends": "eslint:recommended",
                            "parserOptions": {
                                "ecmaVersion": 12,
                                "sourceType": "module"
                            }
                        }
                        
                        config_file = ".eslintrc.json"
                        async with self.file_manager.temporary_file_context(
                            json.dumps(eslint_config).encode('utf-8'),
                            config_file
                        ) as config_id:
                            mounts = self.file_manager.setup_file_mounts([file_id, config_id])
                            command = ["eslint", file_path, "--format", "json"]
                else:
                    # 使用其他代码检查工具
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
                
                # 解析ESLint输出
                if linter == "eslint":
                    errors, warnings = self._parse_eslint_output(output)
                else:
                    # 其他代码检查工具的输出解析
                    errors = [error_output] if error_output else []
                    warnings = []
                
                return JavaScriptCheckResult(
                    success=success,
                    check_type=JavaScriptCheckType.LINT,
                    errors=errors,
                    warnings=warnings,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id,
                    language=language
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"JavaScript/TypeScript代码风格检查超时: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.LINT,
                errors=[f"代码风格检查超时: {e}"],
                execution_time=time.time() - start_time,
                language=language
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"JavaScript/TypeScript代码风格检查失败: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.LINT,
                errors=[f"代码风格检查失败: {e}"],
                execution_time=time.time() - start_time,
                language=language
            )
    
    async def type_check(self,
                        code: str,
                        filename: str = "script.ts",
                        node_version: str = "18") -> JavaScriptCheckResult:
        """
        对TypeScript代码进行类型检查
        
        Args:
            code: TypeScript代码
            filename: 文件名
            node_version: Node.js版本
            
        Returns:
            JavaScriptCheckResult: 类型检查结果
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
                image = self._get_node_image(node_version)
                resource_limits = ResourceLimits(memory="256m", cpu_quota=50000)
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("javascript")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="javascript",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 安装TypeScript
                install_cmd = ["npm", "install", "-g", "typescript"]
                self.container_manager._execute_command(container, install_cmd, timeout=60)
                
                # 创建TypeScript配置文件
                tsconfig = {
                    "compilerOptions": {
                        "target": "es2018",
                        "module": "commonjs",
                        "strict": True,
                        "esModuleInterop": True,
                        "skipLibCheck": True,
                        "forceConsistentCasingInFileNames": True,
                        "noEmit": True
                    },
                    "include": [f"/workspace/{file_id}"]
                }
                
                async with self.file_manager.temporary_file_context(
                    json.dumps(tsconfig).encode('utf-8'),
                    "tsconfig.json"
                ) as config_id:
                    mounts = self.file_manager.setup_file_mounts([file_id, config_id])
                    
                    # 构建类型检查命令
                    file_path = f"/workspace/{file_id}"
                    command = ["npx", "tsc", "--noEmit", file_path]
                    
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
                    errors = [error_output] if error_output else []
                    
                    return JavaScriptCheckResult(
                        success=success,
                        check_type=JavaScriptCheckType.TYPE_CHECK,
                        errors=errors,
                        output=output,
                        execution_time=execution_time,
                        container_id=container_id,
                        language="typescript"
                    )
                
        except ContainerTimeoutError as e:
            logger.error(f"TypeScript类型检查超时: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.TYPE_CHECK,
                errors=[f"类型检查超时: {e}"],
                execution_time=time.time() - start_time,
                language="typescript"
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"TypeScript类型检查失败: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.TYPE_CHECK,
                errors=[f"类型检查失败: {e}"],
                execution_time=time.time() - start_time,
                language="typescript"
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"JavaScript/TypeScript语法检查失败: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time,
                language=language
            )
    
    async def execute_code(self,
                          code: str,
                          filename: str = "script.js",
                          options: Optional[JavaScriptExecutionOptions] = None) -> JavaScriptCheckResult:
        """
        执行JavaScript/TypeScript代码
        
        Args:
            code: JavaScript/TypeScript代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            JavaScriptCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or JavaScriptExecutionOptions()
        language = self._detect_language(code, filename)
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_node_image(options.node_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("javascript")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="javascript",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建执行命令
                file_path = f"{options.working_directory}/{file_id}"
                
                if language == "typescript" or options.typescript:
                    # 对于TypeScript，先安装TypeScript并编译
                    install_cmd = ["npm", "install", "-g", "typescript"]
                    self.container_manager._execute_command(container, install_cmd, timeout=60)
                    
                    # 编译TypeScript到JavaScript
                    compiled_file = file_id.replace('.ts', '.js')
                    compile_cmd = ["npx", "tsc", file_path, "--outFile", compiled_file]
                    self.container_manager._execute_command(container, compile_cmd, timeout=60)
                    
                    # 执行编译后的JavaScript
                    command = ["node", compiled_file]
                else:
                    # 直接执行JavaScript
                    command = ["node", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container,
                    command,
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_javascript_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                return JavaScriptCheckResult(
                    success=success,
                    check_type=JavaScriptCheckType.EXECUTE,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id,
                    language=language
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"JavaScript/TypeScript代码执行超时: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time,
                language=language
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"JavaScript/TypeScript代码执行失败: {e}")
            return JavaScriptCheckResult(
                success=False,
                check_type=JavaScriptCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time,
                language=language
            )