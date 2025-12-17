"""
Rust编译器工具

使用官方Rust Docker镜像实现Rust代码的语法检查、编译、执行和Cargo包管理功能。
支持Rust代码编译、依赖管理和测试功能。
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


class RustCheckType(Enum):
    """Rust检查类型"""
    SYNTAX = "syntax"        # 语法检查
    COMPILE = "compile"      # 编译检查
    EXECUTE = "execute"      # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查
    TEST = "test"           # 测试检查
    CARGO_NEW = "cargo_new"   # 创建新项目
    CARGO_BUILD = "cargo_build"   # 构建项目
    CARGO_CHECK = "cargo_check"   # 检查项目
    CARGO_RUN = "cargo_run"   # 运行项目
    CARGO_TEST = "cargo_test"   # 运行测试
    METADATA = "metadata"    # 元数据生成


@dataclass
class RustCheckResult:
    """Rust检查结果"""
    success: bool
    check_type: RustCheckType
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output: str = ""
    execution_time: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    test_results: List[Dict[str, Any]] = field(default_factory=list)  # 测试结果
    rust_version: str = "1.75"
    container_id: Optional[str] = None
    cargo_info: Dict[str, Any] = field(default_factory=dict)  # Cargo项目信息
    
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
            "rust_version": self.rust_version,
            "container_id": self.container_id,
            "cargo_info": self.cargo_info
        }


@dataclass
class RustExecutionOptions:
    """Rust执行选项"""
    timeout: int = 30
    check_dependencies: bool = True
    download_dependencies: bool = True
    capture_output: bool = True
    working_directory: str = "/workspace"
    rust_version: str = "1.75"
    memory_limit: str = "512m"
    cpu_limit: int = 100000
    project_name: Optional[str] = None  # Cargo项目名
    target_directory: str = "target"  # 目标目录
    release_build: bool = False  # 是否使用release模式
    features: List[str] = field(default_factory=list)  # 特性列表
    verbose: bool = False  # 详细输出
    edition: str = "2021"  # Rust版本(2015, 2018, 2021)


class RustCompilerError(Exception):
    """Rust编译器异常"""
    pass


class RustDependencyError(RustCompilerError):
    """Rust依赖管理异常"""
    pass


class RustSyntaxError(RustCompilerError):
    """Rust语法错误"""
    pass


class RustCargoError(RustCompilerError):
    """Cargo项目管理异常"""
    pass


class RustTestError(RustCompilerError):
    """Rust测试异常"""
    pass


class RustCompiler:
    """Rust编译器工具类"""
    
    # Rust Docker镜像版本映射
    RUST_IMAGES = {
        "1.70": "rust:1.70-slim",
        "1.71": "rust:1.71-slim",
        "1.72": "rust:1.72-slim",
        "1.73": "rust:1.73-slim",
        "1.74": "rust:1.74-slim",
        "1.75": "rust:1.75-slim",
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化Rust编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("Rust编译器初始化完成")
    
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
    
    def _get_rust_image(self, version: str) -> str:
        """获取指定版本的Rust Docker镜像"""
        return self.RUST_IMAGES.get(version, "rust:1.75-slim")
    
    def _parse_rust_error(self, error_output: str) -> List[str]:
        """解析Rust错误输出"""
        errors = []
        
        # Rust错误模式
        patterns = [
            r"error\[E\d+\]:\s*(.+)",  # 编译器错误
            r"warning:\s*(.+)",         # 警告
            r"error:\s*(.+)",           # 一般错误
            r"([^\s:]+):(\d+):\s*(\d+):\s*(.+)",  # 文件名:行号:列号: 错误消息
            r"([^\s:]+):(\d+):\s*(.+)",           # 文件名:行号: 错误消息
            r"help:\s*(.+)",           # 帮助信息
        ]
        
        lines = error_output.split('\n')
        current_error = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # 检查是否是新的错误行
            is_error_start = any(re.search(pattern, line) for pattern in patterns[:4])
            is_note_or_help = line.startswith(("note:", "help:"))
            
            if is_error_start and current_error and not is_note_or_help:
                # 保存上一个错误
                errors.append(' '.join(current_error))
                current_error = [line]
            else:
                current_error.append(line)
        
        # 添加最后一个错误
        if current_error:
            errors.append(' '.join(current_error))
        
        return errors
    
    def _parse_rust_warnings(self, output: str) -> List[str]:
        """解析Rust警告输出"""
        warnings = []
        
        warning_pattern = r"warning:\s*(.+)"
        help_pattern = r"--> ([^\s:]+):(\d+):\s*(\d+)"
        
        lines = output.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("warning:"):
                # 查找相关的位置信息
                warning_info = line
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith(("warning:", "error:", "help:")):
                    if lines[j].strip().startswith("-->"):
                        warning_info += " " + lines[j].strip()
                    j += 1
                warnings.append(warning_info)
        
        return warnings
    
    def _parse_cargo_output(self, output: str) -> Dict[str, Any]:
        """解析Cargo命令输出，提取项目信息"""
        info = {}
        
        # 解析依赖信息
        dependencies = []
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith("Downloading") and ".crate" in line:
                # 提取依赖名称和版本
                match = re.search(r"Downloading\s+([^-]+)-([^.\s]+)", line)
                if match:
                    dependencies.append(f"{match.group(1)} {match.group(2)}")
            elif line.startswith("Updating"):
                info["registry_update"] = line
            elif line.startswith("Compiling"):
                # 提取编译信息
                match = re.search(r"Compiling\s+(\S+)\s+v([^\\n]+)", line)
                if match:
                    info["compiling"] = f"{match.group(1)} v{match.group(2)}"
        
        info["dependencies"] = dependencies
        return info
    
    def _parse_test_output(self, output: str) -> List[Dict[str, Any]]:
        """解析cargo test输出，返回测试结果"""
        test_results = []
        
        # 测试结果模式
        test_run_pattern = r"running\s+(\d+)\s+test"
        test_pass_pattern = r"test\s+(\S+)\s+\.\.\.\s+ok"
        test_fail_pattern = r"test\s+(\S+)\s+\.\.\.\s+FAILED"
        test_summary_pattern = r"test result:\s+(.+)\.\s+(\d+)\s+passed;\s+(\d+)\s+failed;"
        
        lines = output.split('\n')
        current_test = None
        
        for line in lines:
            line = line.strip()
            
            # 检测测试运行
            test_run_match = re.search(test_run_pattern, line)
            if test_run_match:
                continue
            
            # 检测测试通过
            test_pass_match = re.search(test_pass_pattern, line)
            if test_pass_match:
                test_name = test_pass_match.group(1)
                test_results.append({
                    "name": test_name,
                    "status": "passed",
                    "output": []
                })
                continue
            
            # 检测测试失败
            test_fail_match = re.search(test_fail_pattern, line)
            if test_fail_match:
                test_name = test_fail_match.group(1)
                current_test = {
                    "name": test_name,
                    "status": "failed",
                    "output": []
                }
                test_results.append(current_test)
                continue
            
            # 检测测试摘要
            test_summary_match = re.search(test_summary_pattern, line)
            if test_summary_match:
                result_line = test_summary_match.group(1)
                passed = test_summary_match.group(2)
                failed = test_summary_match.group(3)
                test_results.append({
                    "name": "summary",
                    "status": result_line,
                    "passed": int(passed),
                    "failed": int(failed),
                    "output": []
                })
            
            # 收集测试输出
            if current_test and line and not line.startswith("thread") and not line.startswith("note:"):
                current_test["output"].append(line)
        
        return test_results
    
    def _is_cargo_project(self, code: str) -> bool:
        """检测代码是否包含Cargo项目相关内容"""
        return ("fn main()}" in code or "use " in code or "mod " in code or 
                "extern crate" in code or "Cargo.toml" in code)
    
    def _extract_rust_version_from_code(self, code: str) -> str:
        """从Rust代码中推断Rust版本"""
        # 检查代码中的版本特性
        version_features = {
            "1.75": [r"let\s+\w+\s*\|\s*\w+\s*=\s*\w+\.", r"async\s+fn"],
            "1.70": [r"let-else", r"generic\s+statics"],
            "1.68": [r"#[must_use]"],
            "1.65": [r"let\s+_"],
            "1.62": [r"CString"],
            "1.60": [r"#[rustc_const_unstable]"],
            "1.56": [r"c?ad?r?", r"core::array"],
        }
        
        for version, patterns in version_features.items():
            for pattern in patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    return version
        
        # 检查edition字段
        edition_match = re.search(r"edition\s*=\s*[\"'](\d{4})[\"']", code)
        if edition_match:
            edition = edition_match.group(1)
            if edition == "2015":
                return "1.36"  # 2015 edition最早支持的版本
            elif edition == "2018":
                return "1.56"  # 2018 edition需要1.31+
            elif edition == "2021":
                return "1.65"  # 2021 edition需要1.56+
        
        # 默认返回1.75，因为这是当前最新的稳定版本
        return "1.75"
    
    def _suggest_fix_for_error(self, error_message: str) -> Optional[str]:
        """为常见Rust错误提供建议修复方案"""
        error_patterns_suggestions = [
            (r"E0425.*cannot find value", "未找到变量值，请检查变量名是否正确，或者是否在当前作用域内声明"),
            (r"E0277.*doesn't implement", "类型不匹配，请检查泛型约束或者类型转换"),
            (r"E0308.*mismatched types", "类型不匹配，请检查变量类型是否正确"),
            (r"E0061.*function takes", "函数参数数量不匹配，请检查函数调用时参数数量和类型"),
            (r"E0204.*the trait", "trait实现问题，请检查trait方法是否正确实现"),
            (r"E0053.*method has an incompatible type", "方法签名不兼容，请检查方法参数和返回类型"),
            (r"E0599.*no method named", "方法不存在，请检查是否有对应的trait实现或导入"),
            (r"E0432.*unresolved import", "无法解析导入，请检查模块路径是否正确"),
            (r"E0433.*failed to resolve", "路径解析失败，请检查模块结构或使用crate关键字"),
            (r"E0382.*borrow of moved value", "值被移动后再次借用的错误，请考虑使用克隆或重新设计所有权"),
            (r"E0596.*cannot borrow", "借用检查错误，请检查借用规则，可能需要使用可变借用或引用"),
            (r"E0381.*use of moved value", "使用已移动的值，请使用.clone()复制值或重新设计逻辑"),
            (r"E0252.*is defined multiple times", "名称重复定义，请检查是否有重复的导入或声明"),
            (r"E0255.*is not directly importable", "私有模块导入错误，请使用pub关键字或检查模块可见性"),
            (r"E0733.*recursion in an async function", "递归异步函数问题，请使用Box::pin或重构为异步方法"),
            (r"E0063.*missing field", "结构体字段缺失，请检查所有必需字段是否都已初始化"),
            (r"E0282.*type annotations needed", "类型注解不足，请添加明确的类型注解"),
            (r"E0597.*does not live long enough", "生命周期不足，请调整变量生命周期或使用引用"),
            (r"E0106.*missing lifetime specifier", "缺少生命周期说明符，请添加适当的生命周期注解"),
        ]
        
        for pattern, suggestion in error_patterns_suggestions:
            if re.search(pattern, error_message):
                return suggestion
        
        return None
    
    async def check_syntax(self,
                          code: str,
                          filename: str = "main.rs",
                          options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        检查Rust代码语法
        
        Args:
            code: Rust代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                file_path = f"{options.working_directory}/{file_id}"
                
                # 如果是Cargo项目，需要创建Cargo.toml文件
                if self._is_cargo_project(code) and options.project_name:
                    # 创建Cargo.toml文件
                    cargo_toml_content = f"""[package]
name = "{options.project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                    async with self.file_manager.temporary_file_context(
                        cargo_toml_content.encode('utf-8'),
                        "Cargo.toml"
                    ) as cargo_file_id:
                        # 创建src目录和main.rs
                        # 更新挂载
                        mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                        # 重新获取容器
                        container = self.container_manager.client.containers.get(container_id)
                
                # 构建语法检查命令
                command = ["rustc", "--emit=metadata", file_path]
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container,
                    command,
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_rust_error(stderr.decode('utf-8')) if stderr else []
                warnings = self._parse_rust_warnings(stderr.decode('utf-8')) if stderr else []
                
                return RustCheckResult(
                    success=success,
                    check_type=RustCheckType.SYNTAX,
                    errors=errors,
                    warnings=warnings,
                    output=stdout.decode('utf-8') if stdout else "",
                    execution_time=execution_time,
                    rust_version=options.rust_version,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Rust语法检查超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
    
    async def cargo_run(self,
                       code: str,
                       filename: str = "main.rs",
                       options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        使用Cargo运行Rust代码
        
        Args:
            code: Rust代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 运行结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 创建Cargo.toml文件
                project_name = options.project_name or "rust_project"
                cargo_toml_content = f"""[package]
name = "{project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                async with self.file_manager.temporary_file_context(
                    cargo_toml_content.encode('utf-8'),
                    "Cargo.toml"
                ) as cargo_file_id:
                    # 设置文件挂载
                    mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                    
                    # 创建src目录并将文件移动到src目录
                    src_file_path = f"{options.working_directory}/src/main.rs"
                    
                    # 获取容器实例
                    container = self.container_manager.client.containers.get(container_id)
                    
                    # 创建src目录
                    mkdir_command = ["mkdir", "-p", f"{options.working_directory}/src"]
                    self.container_manager._execute_command(
                        container,
                        mkdir_command,
                        timeout=5
                    )
                    
                    # 复制文件到src目录
                    copy_command = ["cp", f"{options.working_directory}/{file_id}", src_file_path]
                    self.container_manager._execute_command(
                        container,
                        copy_command,
                        timeout=5
                    )
                    
                    # 构建运行命令
                    command = ["cargo", "run"]
                    
                    # 添加release模式
                    if options.release_build:
                        command.append("--release")
                    
                    # 添加详细输出
                    if options.verbose:
                        command.append("--verbose")
                    
                    # 执行命令
                    exit_code, stdout, stderr = self.container_manager._execute_command(
                        container,
                        command,
                        timeout=options.timeout
                    )
                    
                    execution_time = time.time() - start_time
                    
                    # 解析结果
                    success = exit_code == 0
                    errors = self._parse_rust_error(stderr.decode('utf-8')) if stderr else []
                    warnings = self._parse_rust_warnings(stderr.decode('utf-8')) if stdout else []
                    
                    # 解析Cargo信息
                    cargo_info = self._parse_cargo_output(stdout.decode('utf-8') + '\n' +
                                                       (stderr.decode('utf-8') if stderr else ''))
                    
                    return RustCheckResult(
                        success=success,
                        check_type=RustCheckType.CARGO_RUN,
                        errors=errors,
                        warnings=warnings,
                        output=stdout.decode('utf-8') if stdout else "",
                        execution_time=execution_time,
                        rust_version=options.rust_version,
                        container_id=container_id,
                        cargo_info=cargo_info
                    )
                    
        except ContainerTimeoutError as e:
            logger.error(f"Cargo运行超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_RUN,
                errors=[f"Cargo运行超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Cargo运行失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_RUN,
                errors=[f"Cargo运行失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
    
    async def cargo_test(self,
                        code: str,
                        test_code: Optional[str] = None,
                        filename: str = "main.rs",
                        options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        使用Cargo测试Rust代码
        
        Args:
            code: Rust代码
            test_code: 测试代码，如果为None则查找代码中的测试
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 测试结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 创建Cargo.toml文件
                project_name = options.project_name or "rust_project"
                cargo_toml_content = f"""[package]
name = "{project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                async with self.file_manager.temporary_file_context(
                    cargo_toml_content.encode('utf-8'),
                    "Cargo.toml"
                ) as cargo_file_id:
                    # 设置文件挂载
                    mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                    
                    # 创建src目录并将文件移动到src目录
                    src_file_path = f"{options.working_directory}/src/main.rs"
                    
                    # 获取容器实例
                    container = self.container_manager.client.containers.get(container_id)
                    
                    # 创建src目录
                    mkdir_command = ["mkdir", "-p", f"{options.working_directory}/src"]
                    self.container_manager._execute_command(
                        container,
                        mkdir_command,
                        timeout=5
                    )
                    
                    # 复制文件到src目录
                    copy_command = ["cp", f"{options.working_directory}/{file_id}", src_file_path]
                    self.container_manager._execute_command(
                        container,
                        copy_command,
                        timeout=5
                    )
                    
                    # 如果有额外的测试代码，将其添加到lib.rs中
                    if test_code:
                        async with self.file_manager.temporary_file_context(
                            test_code.encode('utf-8'),
                            "lib.rs"
                        ) as test_file_id:
                            # 复制测试文件到src目录
                            test_src_path = f"{options.working_directory}/src/lib.rs"
                            copy_test_command = ["cp", f"{options.working_directory}/{test_file_id}", test_src_path]
                            self.container_manager._execute_command(
                                container,
                                copy_test_command,
                                timeout=5
                            )
                    
                    # 构建测试命令
                    command = ["cargo", "test"]
                    
                    # 添加release模式
                    if options.release_build:
                        command.append("--release")
                    
                    # 添加详细输出
                    if options.verbose:
                        command.append("--verbose")
                    
                    # 执行命令
                    exit_code, stdout, stderr = self.container_manager._execute_command(
                        container,
                        command,
                        timeout=options.timeout
                    )
                    
                    execution_time = time.time() - start_time
                    
                    # 解析结果
                    success = exit_code == 0
                    errors = self._parse_rust_error(stderr.decode('utf-8')) if stderr else []
                    warnings = self._parse_rust_warnings(stderr.decode('utf-8')) if stdout else []
                    
                    # 解析Cargo信息
                    cargo_info = self._parse_cargo_output(stdout.decode('utf-8') + '\n' +
                                                       (stderr.decode('utf-8') if stderr else ''))
                    
                    # 解析测试结果
                    test_output = stdout.decode('utf-8') if stdout else ""
                    test_output += '\n' + (stderr.decode('utf-8') if stderr else "")
                    test_results = self._parse_test_output(test_output)
                    
                    return RustCheckResult(
                        success=success,
                        check_type=RustCheckType.CARGO_TEST,
                        errors=errors,
                        warnings=warnings,
                        output=test_output,
                        execution_time=execution_time,
                        rust_version=options.rust_version,
                        container_id=container_id,
                        cargo_info=cargo_info,
                        test_results=test_results
                    )
                    
        except ContainerTimeoutError as e:
            logger.error(f"Cargo测试超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_TEST,
                errors=[f"Cargo测试超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Cargo测试失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_TEST,
                errors=[f"Cargo测试失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
    
    async def cargo_check(self,
                         code: str,
                         filename: str = "main.rs",
                         options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        使用Cargo检查Rust代码
        
        Args:
            code: Rust代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 创建Cargo.toml文件
                project_name = options.project_name or "rust_project"
                cargo_toml_content = f"""[package]
name = "{project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                async with self.file_manager.temporary_file_context(
                    cargo_toml_content.encode('utf-8'),
                    "Cargo.toml"
                ) as cargo_file_id:
                    # 设置文件挂载
                    mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                    
                    # 创建src目录并将文件移动到src目录
                    src_file_path = f"{options.working_directory}/src/main.rs"
                    
                    # 获取容器实例
                    container = self.container_manager.client.containers.get(container_id)
                    
                    # 创建src目录
                    mkdir_command = ["mkdir", "-p", f"{options.working_directory}/src"]
                    self.container_manager._execute_command(
                        container,
                        mkdir_command,
                        timeout=5
                    )
                    
                    # 复制文件到src目录
                    copy_command = ["cp", f"{options.working_directory}/{file_id}", src_file_path]
                    self.container_manager._execute_command(
                        container,
                        copy_command,
                        timeout=5
                    )
                    
                    # 构建检查命令
                    command = ["cargo", "check"]
                    
                    # 添加release模式
                    if options.release_build:
                        command.append("--release")
                    
                    # 添加详细输出
                    if options.verbose:
                        command.append("--verbose")
                    
                    # 执行命令
                    exit_code, stdout, stderr = self.container_manager._execute_command(
                        container,
                        command,
                        timeout=options.timeout
                    )
                    
                    execution_time = time.time() - start_time
                    
                    # 解析结果
                    success = exit_code == 0
                    errors = self._parse_rust_error(stderr.decode('utf-8')) if stderr else []
                    warnings = self._parse_rust_warnings(stderr.decode('utf-8')) if stdout else []
                    
                    # 解析Cargo信息
                    cargo_info = self._parse_cargo_output(stdout.decode('utf-8') + '\n' +
                                                       (stderr.decode('utf-8') if stderr else ''))
                    
                    return RustCheckResult(
                        success=success,
                        check_type=RustCheckType.CARGO_CHECK,
                        errors=errors,
                        warnings=warnings,
                        output=stdout.decode('utf-8') if stdout else "",
                        execution_time=execution_time,
                        rust_version=options.rust_version,
                        container_id=container_id,
                        cargo_info=cargo_info
                    )
                    
        except ContainerTimeoutError as e:
            logger.error(f"Cargo检查超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_CHECK,
                errors=[f"Cargo检查超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Cargo检查失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_CHECK,
                errors=[f"Cargo检查失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
    
    async def cargo_build(self,
                         code: str,
                         filename: str = "main.rs",
                         options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        使用Cargo构建Rust代码
        
        Args:
            code: Rust代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 构建结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 创建Cargo.toml文件
                project_name = options.project_name or "rust_project"
                cargo_toml_content = f"""[package]
name = "{project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                async with self.file_manager.temporary_file_context(
                    cargo_toml_content.encode('utf-8'),
                    "Cargo.toml"
                ) as cargo_file_id:
                    # 设置文件挂载
                    mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                    
                    # 创建src目录并将文件移动到src目录
                    src_file_path = f"{options.working_directory}/src/main.rs"
                    
                    # 获取容器实例
                    container = self.container_manager.client.containers.get(container_id)
                    
                    # 创建src目录
                    mkdir_command = ["mkdir", "-p", f"{options.working_directory}/src"]
                    self.container_manager._execute_command(
                        container,
                        mkdir_command,
                        timeout=5
                    )
                    
                    # 复制文件到src目录
                    copy_command = ["cp", f"{options.working_directory}/{file_id}", src_file_path]
                    self.container_manager._execute_command(
                        container,
                        copy_command,
                        timeout=5
                    )
                    
                    # 构建构建命令
                    command = ["cargo", "build"]
                    
                    # 添加release模式
                    if options.release_build:
                        command.append("--release")
                    
                    # 添加详细输出
                    if options.verbose:
                        command.append("--verbose")
                    
                    # 执行命令
                    exit_code, stdout, stderr = self.container_manager._execute_command(
                        container,
                        command,
                        timeout=options.timeout
                    )
                    
                    execution_time = time.time() - start_time
                    
                    # 解析结果
                    success = exit_code == 0
                    errors = self._parse_rust_error(stderr.decode('utf-8')) if stderr else []
                    warnings = self._parse_rust_warnings(stderr.decode('utf-8')) if stdout else []
                    
                    # 解析Cargo信息
                    cargo_info = self._parse_cargo_output(stdout.decode('utf-8') + '\n' +
                                                       (stderr.decode('utf-8') if stderr else ''))
                    
                    return RustCheckResult(
                        success=success,
                        check_type=RustCheckType.CARGO_BUILD,
                        errors=errors,
                        warnings=warnings,
                        output=stdout.decode('utf-8') if stdout else "",
                        execution_time=execution_time,
                        rust_version=options.rust_version,
                        container_id=container_id,
                        cargo_info=cargo_info
                    )
                    
        except ContainerTimeoutError as e:
            logger.error(f"Cargo构建超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_BUILD,
                errors=[f"Cargo构建超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Cargo构建失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.CARGO_BUILD,
                errors=[f"Cargo构建失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
    
    async def execute_code(self,
                          code: str,
                          filename: str = "main.rs",
                          options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        执行Rust代码
        
        Args:
            code: Rust代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建执行命令
                file_path = f"{options.working_directory}/{file_id}"
                
                # 如果是Cargo项目，需要创建Cargo.toml文件
                if self._is_cargo_project(code) and options.project_name:
                    # 创建Cargo.toml文件
                    cargo_toml_content = f"""[package]
name = "{options.project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                    async with self.file_manager.temporary_file_context(
                        cargo_toml_content.encode('utf-8'),
                        "Cargo.toml"
                    ) as cargo_file_id:
                        # 创建src目录和main.rs
                        # 更新挂载
                        mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                        # 重新获取容器
                        container = self.container_manager.client.containers.get(container_id)
                
                # 首先编译代码
                compile_command = ["rustc"]
                output_file = f"{options.working_directory}/output"
                compile_command.extend(["-o", output_file, file_path])
                
                # 执行编译命令
                compile_exit_code, compile_stdout, compile_stderr = self.container_manager._execute_command(
                    container,
                    compile_command,
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 如果编译失败，返回错误结果
                if compile_exit_code != 0:
                    errors = self._parse_rust_error(compile_stderr.decode('utf-8')) if compile_stderr else []
                    warnings = self._parse_rust_warnings(compile_stderr.decode('utf-8')) if compile_stderr else []
                    
                    return RustCheckResult(
                        success=False,
                        check_type=RustCheckType.EXECUTE,
                        errors=errors,
                        warnings=warnings,
                        output=compile_stdout.decode('utf-8') if compile_stdout else "",
                        execution_time=execution_time,
                        rust_version=options.rust_version,
                        container_id=container_id
                    )
                
                # 编译成功，执行编译后的程序
                execute_command = [output_file]
                
                # 执行命令
                execute_exit_code, execute_stdout, execute_stderr = self.container_manager._execute_command(
                    container,
                    execute_command,
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = execute_exit_code == 0
                errors = self._parse_rust_error(execute_stderr.decode('utf-8')) if execute_stderr else []
                warnings = self._parse_rust_warnings(execute_stderr.decode('utf-8')) if execute_stderr else []
                
                return RustCheckResult(
                    success=success,
                    check_type=RustCheckType.EXECUTE,
                    errors=errors,
                    warnings=warnings,
                    output=execute_stdout.decode('utf-8') if execute_stdout else "",
                    execution_time=execution_time,
                    rust_version=options.rust_version,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Rust代码执行超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Rust代码执行失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
    
    async def compile_code(self,
                          code: str,
                          filename: str = "main.rs",
                          options: Optional[RustExecutionOptions] = None) -> RustCheckResult:
        """
        编译Rust代码
        
        Args:
            code: Rust代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            RustCheckResult: 编译结果
        """
        start_time = time.time()
        options = options or RustExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_rust_image(options.rust_version)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("rust")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="rust",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 设置文件挂载
                mounts = self.file_manager.setup_file_mounts([file_id])
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建编译命令
                file_path = f"{options.working_directory}/{file_id}"
                
                # 如果是Cargo项目，需要创建Cargo.toml文件
                if self._is_cargo_project(code) and options.project_name:
                    # 创建Cargo.toml文件
                    cargo_toml_content = f"""[package]
name = "{options.project_name}"
version = "0.1.0"
edition = "{options.edition}"

[dependencies]
"""
                    async with self.file_manager.temporary_file_context(
                        cargo_toml_content.encode('utf-8'),
                        "Cargo.toml"
                    ) as cargo_file_id:
                        # 创建src目录和main.rs
                        # 更新挂载
                        mounts = self.file_manager.setup_file_mounts([file_id, cargo_file_id])
                        # 重新获取容器
                        container = self.container_manager.client.containers.get(container_id)
                
                # 构建编译命令
                command = ["rustc"]
                
                # 添加release模式
                if options.release_build:
                    command.append("-O")
                
                # 添加目标文件路径
                output_file = f"{options.working_directory}/output"
                command.extend(["-o", output_file, file_path])
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container,
                    command,
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_rust_error(stderr.decode('utf-8')) if stderr else []
                warnings = self._parse_rust_warnings(stderr.decode('utf-8')) if stderr else []
                
                return RustCheckResult(
                    success=success,
                    check_type=RustCheckType.COMPILE,
                    errors=errors,
                    warnings=warnings,
                    output=stdout.decode('utf-8') if stdout else "",
                    execution_time=execution_time,
                    rust_version=options.rust_version,
                    container_id=container_id
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Rust代码编译超时: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.COMPILE,
                errors=[f"代码编译超时: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Rust代码编译失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.COMPILE,
                errors=[f"代码编译失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Rust语法检查失败: {e}")
            return RustCheckResult(
                success=False,
                check_type=RustCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time,
                rust_version=options.rust_version
            )