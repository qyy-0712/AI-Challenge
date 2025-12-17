"""
C/C++编译器工具

使用官方GCC Docker镜像实现C/C++代码的语法检查、编译、执行和依赖管理功能。
支持多种编译器和标准，以及Make/CMake构建系统。
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


class CppCheckType(Enum):
    """C/C++检查类型"""
    SYNTAX = "syntax"  # 语法检查
    COMPILE = "compile"  # 编译检查
    EXECUTE = "execute"  # 代码执行
    DEPENDENCIES = "dependencies"  # 依赖检查
    MAKE_BUILD = "make_build"  # Make构建
    CMAKE_BUILD = "cmake_build"  # CMake构建


class CppCompilerType(Enum):
    """C/C++编译器类型"""
    GCC = "gcc"
    GPP = "g++"
    CLANG = "clang"
    CLANGPP = "clang++"


class CppStandard(Enum):
    """C/C++标准"""
    # C标准
    C89 = "c89"
    C99 = "c99"
    C11 = "c11"
    C17 = "c17"
    C23 = "c23"
    
    # C++标准
    CPP98 = "c++98"
    CPP03 = "c++03"
    CPP11 = "c++11"
    CPP14 = "c++14"
    CPP17 = "c++17"
    CPP20 = "c++20"
    CPP23 = "c++23"


@dataclass
class CppCheckResult:
    """C/C++检查结果"""
    success: bool
    check_type: CppCheckType
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output: str = ""
    execution_time: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    object_files: List[str] = field(default_factory=list)  # 编译生成的目标文件
    executable_files: List[str] = field(default_factory=list)  # 生成的可执行文件
    container_id: Optional[str] = None
    compiler_type: Optional[CppCompilerType] = None
    standard: Optional[CppStandard] = None
    
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
            "object_files": self.object_files,
            "executable_files": self.executable_files,
            "container_id": self.container_id,
            "compiler_type": self.compiler_type.value if self.compiler_type else None,
            "standard": self.standard.value if self.standard else None
        }


@dataclass
class CppExecutionOptions:
    """C/C++执行选项"""
    timeout: int = 60  # C/C++编译可能需要更多时间
    check_dependencies: bool = True
    install_dependencies: bool = True
    capture_output: bool = True
    working_directory: str = "/workspace"
    compiler_type: CppCompilerType = CppCompilerType.GCC
    standard: Optional[CppStandard] = None
    memory_limit: str = "256m"
    cpu_limit: int = 50000
    optimization_level: str = "-O2"  # 优化级别
    debug_symbols: bool = False  # 是否包含调试符号
    warnings_as_errors: bool = False  # 是否将警告视为错误
    include_paths: List[str] = field(default_factory=list)  # 头文件路径
    library_paths: List[str] = field(default_factory=list)  # 库文件路径
    libraries: List[str] = field(default_factory=list)  # 链接库
    compiler_flags: List[str] = field(default_factory=list)  # 额外编译标志
    linker_flags: List[str] = field(default_factory=list)  # 链接器标志
    build_tool: Optional[str] = None  # 构建工具: make, cmake
    make_targets: List[str] = field(default_factory=list)  # Make目标
    cmake_args: List[str] = field(default_factory=list)  # CMake参数


class CppCompilerError(Exception):
    """C/C++编译器异常"""
    pass


class CppDependencyError(CppCompilerError):
    """C/C++依赖管理异常"""
    pass


class CppSyntaxError(CppCompilerError):
    """C/C++语法错误"""
    pass


class CppBuildError(CppCompilerError):
    """C/C++构建错误"""
    pass


class CppCompiler:
    """C/C++编译器工具类"""
    
    # C/C++ Docker镜像版本映射
    CPP_IMAGES = {
        CppCompilerType.GCC: "gcc:latest",
        CppCompilerType.GPP: "gcc:latest",
        CppCompilerType.CLANG: "gcc:latest",  # 使用GCC镜像，但安装clang
        CppCompilerType.CLANGPP: "gcc:latest",  # 使用GCC镜像，但安装clang++
    }
    
    # 编译器命令映射
    COMPILER_COMMANDS = {
        CppCompilerType.GCC: "gcc",
        CppCompilerType.GPP: "g++",
        CppCompilerType.CLANG: "clang",
        CppCompilerType.CLANGPP: "clang++",
    }
    
    # 文件扩展名映射
    FILE_EXTENSIONS = {
        ".c": CppCompilerType.GCC,
        ".cpp": CppCompilerType.GPP,
        ".cxx": CppCompilerType.GPP,
        ".cc": CppCompilerType.GPP,
        ".c++": CppCompilerType.GPP,
        ".h": CppCompilerType.GCC,
        ".hpp": CppCompilerType.GPP,
        ".hxx": CppCompilerType.GPP,
    }
    
    # 默认编译标准
    DEFAULT_STANDARDS = {
        CppCompilerType.GCC: CppStandard.C11,
        CppCompilerType.GPP: CppStandard.CPP17,
        CppCompilerType.CLANG: CppStandard.C11,
        CppCompilerType.CLANGPP: CppStandard.CPP17,
    }
    
    def __init__(self, 
                 container_manager: Optional[ContainerManager] = None,
                 file_manager: Optional[FileManager] = None):
        """
        初始化C/C++编译器
        
        Args:
            container_manager: 容器管理器实例
            file_manager: 文件管理器实例
        """
        self.container_manager = container_manager or ContainerManager()
        self.file_manager = file_manager or FileManager()
        
        # 注意：清理任务将在第一次使用时启动
        self._cleanup_tasks_started = False
        
        logger.info("C/C++编译器初始化完成")
    
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
    
    def _get_cpp_image(self, compiler_type: CppCompilerType) -> str:
        """获取指定编译器类型的Docker镜像"""
        return self.CPP_IMAGES.get(compiler_type, "gcc:latest")
    
    def _detect_language_from_file(self, filename: str) -> CppCompilerType:
        """从文件名检测C/C++语言类型"""
        ext = Path(filename).suffix.lower()
        return self.FILE_EXTENSIONS.get(ext, CppCompilerType.GCC)
    
    def _detect_language_from_code(self, code: str) -> CppCompilerType:
        """从代码内容检测C/C++语言类型"""
        # 简单的启发式检测
        if re.search(r'#include\s*<iostream>|using\s+namespace\s+std|std::', code):
            return CppCompilerType.GPP
        elif re.search(r'#include\s*<cstdio>|printf\s*\(|scanf\s*\(', code):
            return CppCompilerType.GCC
        
        # 默认返回GPP
        return CppCompilerType.GPP
    
    def _infer_standard_from_code(self, code: str, compiler_type: CppCompilerType) -> CppStandard:
        """从代码推断编译标准"""
        # C++特性检测
        cpp_features = {
            CppStandard.CPP23: [r"std::mdspan", r"std::expected", r"std::flat_map", r"std::flat_set"],
            CppStandard.CPP20: [r"std::format", r"std::span", r"std::jthread", r"concept\s+\w+", r"requires\s*\("],
            CppStandard.CPP17: [r"std::optional", r"std::variant", r"std::any", r"std::string_view", r"if\s+constexpr"],
            CppStandard.CPP14: [r"auto\s+return", r"generic\s+lambda", r"decltype\s*\(", r"std::make_unique"],
            CppStandard.CPP11: [r"std::thread", r"std::mutex", r"std::condition_variable", r"std::atomic", r"override", r"final"],
            CppStandard.CPP03: [r"std::auto_ptr", r"std::tr1::"],
        }
        
        # C特性检测
        c_features = {
            CppStandard.C23: [r"typeof\s*\(", r"static_assert\s*\(", r"_Generic\s*\("],
            CppStandard.C17: [r"std::atomic_int", r"std::atomic_flag"],
            CppStandard.C11: [r"_Thread_local", r"_Alignas\s*\(", r"_Alignof\s*\("],
            CppStandard.C99: [r"for\s*\([^;]*;\s*[^;]*;\s*[^)]*\)", r"//.*$", r"restricted\s+"],
            CppStandard.C89: [r"printf\s*\(", r"scanf\s*\("],
        }
        
        # 根据编译器类型选择特性集
        if compiler_type in [CppCompilerType.GPP, CppCompilerType.CLANGPP]:
            features = cpp_features
            default_standard = CppStandard.CPP17
        else:
            features = c_features
            default_standard = CppStandard.C11
        
        # 从最新标准开始检测
        for standard in sorted(features.keys(), key=lambda x: x.value, reverse=True):
            for pattern in features[standard]:
                if re.search(pattern, code, re.MULTILINE):
                    return standard
        
        # 返回默认标准
        return default_standard
    
    def _parse_cpp_error(self, error_output: str) -> List[str]:
        """解析C/C++错误输出"""
        errors = []
        
        # C/C++错误模式
        patterns = [
            r"([^:]+):(\d+):(\d+):\s*(error|warning|note):\s*(.*)",  # 文件名:行号:列号: 类型: 消息
            r"([^:]+):(\d+):\s*(error|warning|note):\s*(.*)",  # 文件名:行号: 类型: 消息
            r"([A-Za-z0-9_]+):\s*(error|warning):\s*(.*)",  # 编译器错误
            r"undefined reference to\s+`([^']+)'",  # 链接错误
            r"multiple definition of\s+`([^']+)'",  # 重复定义错误
            r"fatal error:\s*(.*)",  # 致命错误
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
    
    def _parse_make_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析Make输出，返回(目标信息, 错误信息)"""
        targets = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测目标信息
            match = re.search(r'making\s+target\s+`([^\'\']+)', line, re.IGNORECASE)
            if match:
                targets.append(match.group(1))
            
            # 检测错误
            if line.startswith("make:") and ("error" in line.lower() or "failed" in line.lower()):
                errors.append(line)
            elif re.search(r'error:', line, re.IGNORECASE):
                errors.append(line)
        
        return targets, errors
    
    def _parse_cmake_output(self, output: str) -> Tuple[List[str], List[str]]:
        """解析CMake输出，返回(配置信息, 错误信息)"""
        configs = []
        errors = []
        
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            
            # 检测配置信息
            match = re.search(r'--\s+(.*)', line)
            if match:
                configs.append(match.group(1))
            
            # 检测错误
            if line.startswith("CMake Error:") or line.startswith("CMake Warning:"):
                errors.append(line)
            elif re.search(r'error:', line, re.IGNORECASE):
                errors.append(line)
        
        return configs, errors
    
    def _is_project_structure(self, files: List[str]) -> Tuple[bool, Optional[str]]:
        """检测是否为项目结构，返回(是否为项目, 构建工具类型)"""
        has_makefile = any(f.lower().endswith("makefile") or f.lower() == "makefile" for f in files)
        has_cmake = any(f.lower() == "cmakelists.txt" for f in files)
        
        if has_cmake:
            return True, "cmake"
        elif has_makefile:
            return True, "make"
        
        return False, None
    
    def _suggest_fix_for_error(self, error_message: str) -> Optional[str]:
        """为常见C/C++错误提供建议修复方案"""
        error_patterns_suggestions = [
            (r"undefined reference to\s+`([^']+)'", "未定义的引用，请检查是否包含了相应的头文件和链接了正确的库"),
            (r"multiple definition of\s+`([^']+)'", "多重定义，请检查是否在头文件中定义了变量或函数，应该只声明不定义"),
            (r"fatal error:\s*([^:]+):\s*No such file or directory", "找不到文件，请检查文件路径是否正确或是否包含了正确的头文件路径"),
            (r"error:\s*expected\s+[\'\"]?[;{}\'\"]", "语法错误，缺少分号、括号或大括号，请检查代码语法"),
            (r"error:\s*\'([^\']+)\'\s+was\s+not\s+declared\s+in\s+this\s+scope", "变量或函数未声明，请检查拼写或是否包含了相应的头文件"),
            (r"error:\s*invalid\s+conversion\s+from\s+[\'\"]?([^\'\"]+)[\'\"]?\s+to\s+[\'\"]?([^\'\"]+)[\'\"]?", "类型转换错误，请检查类型是否兼容或使用适当的类型转换"),
            (r"error:\s*cannot\s+convert\s+[\'\"]?([^\'\"]+)[\'\"]?\s+to\s+[\'\"]?([^\'\"]+)[\'\"]?", "类型转换错误，请检查类型是否兼容或使用适当的类型转换"),
            (r"warning:\s*unused\s+variable\s+[\'\"]?([^\'\"]+)[\'\"]?", "未使用的变量，请考虑删除或使用该变量"),
            (r"warning:\s*control\s+reaches\s+end\s+of\s+non-void\s+function", "函数没有返回值，请确保所有代码路径都有返回语句"),
            (r"warning:\s*implicit\s+declaration\s+of\s+function", "函数隐式声明，请确保包含了函数的正确头文件"),
            (r"segmentation\s+fault", "段错误，可能是访问了无效的内存地址，请检查指针使用和数组边界"),
            (r"Bus\s+error", "总线错误，通常是对齐问题或访问了无效内存地址"),
            (r"make:\s*\*\*\*\s*\[.+\]\s+Error\s+\d+", "Make构建失败，请检查Makefile和源代码"),
            (r"CMake\s+Error", "CMake配置失败，请检查CMakeLists.txt文件"),
        ]
        
        for pattern, suggestion in error_patterns_suggestions:
            if re.search(pattern, error_message, re.IGNORECASE):
                return suggestion
        
        return None
    
    def _build_compiler_command(self, 
                               compiler_type: CppCompilerType, 
                               file_path: str,
                               options: CppExecutionOptions,
                               syntax_only: bool = False) -> List[str]:
        """构建编译器命令"""
        command = [self.COMPILER_COMMANDS[compiler_type]]
        
        # 添加编译标准
        if options.standard:
            if "c++" in options.standard.value:
                command.append(f"-std={options.standard.value}")
            else:
                command.append(f"-std={options.standard.value}")
        
        # 添加优化级别
        if options.optimization_level:
            command.append(options.optimization_level)
        
        # 添加调试符号
        if options.debug_symbols:
            command.append("-g")
        
        # 添加警告设置
        if options.warnings_as_errors:
            command.append("-Werror")
        else:
            command.append("-Wall")
            command.append("-Wextra")
        
        # 添加头文件路径
        for path in options.include_paths:
            command.extend(["-I", path])
        
        # 添加库文件路径
        for path in options.library_paths:
            command.extend(["-L", path])
        
        # 添加链接库
        for lib in options.libraries:
            command.extend(["-l", lib])
        
        # 添加额外编译标志
        command.extend(options.compiler_flags)
        
        # 添加链接器标志
        if not syntax_only:  # 只在非语法检查时添加链接器标志
            command.extend(options.linker_flags)
        
        # 添加语法检查标志
        if syntax_only:
            command.append("-fsyntax-only")
        
        # 添加输出文件（非语法检查时）
        if not syntax_only:
            output_file = file_path.rsplit('.', 1)[0]
            command.extend(["-o", output_file])
        
        # 添加源文件
        command.append(file_path)
        
        return command
    
    async def enhanced_check_syntax(self, 
                                   code: str, 
                                   filename: str = "main.cpp",
                                   options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        增强的C/C++代码语法检查，包含错误分析和修复建议
        
        Args:
            code: C/C++代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            CppCheckResult: 检查结果
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
        
        # 自动检测编译器类型（如果未指定）
        if options and not options.compiler_type:
            detected_type = self._detect_language_from_file(filename)
            result.compiler_type = detected_type
        
        # 自动推断编译标准（如果未指定）
        if options and not options.standard:
            inferred_standard = self._infer_standard_from_code(code, result.compiler_type or CppCompilerType.GPP)
            result.standard = inferred_standard
        
        return result
    
    async def check_syntax(self, 
                          code: str, 
                          filename: str = "main.cpp",
                          options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        检查C/C++代码语法
        
        Args:
            code: C/C++代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            CppCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or CppExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 检测编译器类型
            compiler_type = options.compiler_type or self._detect_language_from_file(filename)
            
            # 推断编译标准
            standard = options.standard or self._infer_standard_from_code(code, compiler_type)
            
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_cpp_image(compiler_type)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                language = "cpp" if compiler_type in [CppCompilerType.GPP, CppCompilerType.CLANGPP] else "c"
                container_id = self.container_manager.get_container_for_language(language)
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language=language,
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 安装clang（如果需要）
                if compiler_type in [CppCompilerType.CLANG, CppCompilerType.CLANGPP]:
                    try:
                        self.container_manager._execute_command(
                            container, 
                            ["apt-get", "update"], 
                            timeout=30
                        )
                        self.container_manager._execute_command(
                            container, 
                            ["apt-get", "install", "-y", "clang"], 
                            timeout=120
                        )
                    except Exception as e:
                        logger.warning(f"安装clang失败，回退到gcc: {e}")
                        compiler_type = CppCompilerType.GCC if compiler_type == CppCompilerType.CLANG else CppCompilerType.GPP
                
                # 构建语法检查命令
                file_path = f"{options.working_directory}/{file_id}"
                command = self._build_compiler_command(
                    compiler_type, 
                    file_path, 
                    options, 
                    syntax_only=True
                )
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_cpp_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                return CppCheckResult(
                    success=success,
                    check_type=CppCheckType.SYNTAX,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    container_id=container_id,
                    compiler_type=compiler_type,
                    standard=standard
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"C/C++语法检查超时: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.SYNTAX,
                errors=[f"语法检查超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"C/C++语法检查失败: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.SYNTAX,
                errors=[f"语法检查失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def compile_code(self, 
                          code: str, 
                          filename: str = "main.cpp",
                          options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        编译C/C++代码
        
        Args:
            code: C/C++代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            CppCheckResult: 编译结果
        """
        start_time = time.time()
        options = options or CppExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 检测编译器类型
            compiler_type = options.compiler_type or self._detect_language_from_file(filename)
            
            # 推断编译标准
            standard = options.standard or self._infer_standard_from_code(code, compiler_type)
            
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'), 
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_cpp_image(compiler_type)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                language = "cpp" if compiler_type in [CppCompilerType.GPP, CppCompilerType.CLANGPP] else "c"
                container_id = self.container_manager.get_container_for_language(language)
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language=language,
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 安装clang（如果需要）
                if compiler_type in [CppCompilerType.CLANG, CppCompilerType.CLANGPP]:
                    try:
                        self.container_manager._execute_command(
                            container, 
                            ["apt-get", "update"], 
                            timeout=30
                        )
                        self.container_manager._execute_command(
                            container, 
                            ["apt-get", "install", "-y", "clang"], 
                            timeout=120
                        )
                    except Exception as e:
                        logger.warning(f"安装clang失败，回退到gcc: {e}")
                        compiler_type = CppCompilerType.GCC if compiler_type == CppCompilerType.CLANG else CppCompilerType.GPP
                
                # 构建编译命令
                file_path = f"{options.working_directory}/{file_id}"
                command = self._build_compiler_command(
                    compiler_type, 
                    file_path, 
                    options, 
                    syntax_only=False
                )
                
                # 执行命令
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container, 
                    command, 
                    timeout=options.timeout
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                errors = self._parse_cpp_error(stderr.decode('utf-8')) if stderr else []
                output = stdout.decode('utf-8') if stdout else ""
                
                # 收集生成的文件
                object_files = []
                executable_files = []
                
                if success:
                    # 获取输出文件名
                    output_file = file_path.rsplit('.', 1)[0]
                    executable_files.append(output_file)
                
                return CppCheckResult(
                    success=success,
                    check_type=CppCheckType.COMPILE,
                    errors=errors,
                    warnings=[],  # TODO: 解析警告
                    output=output,
                    execution_time=execution_time,
                    object_files=object_files,
                    executable_files=executable_files,
                    container_id=container_id,
                    compiler_type=compiler_type,
                    standard=standard
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"C/C++代码编译超时: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.COMPILE,
                errors=[f"代码编译超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"C/C++代码编译失败: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.COMPILE,
                errors=[f"代码编译失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def execute_code(self, 
                          code: str, 
                          filename: str = "main.cpp",
                          options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        执行C/C++代码
        
        Args:
            code: C/C++代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            CppCheckResult: 执行结果
        """
        start_time = time.time()
        options = options or CppExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 先编译代码
            compile_result = await self.compile_code(code, filename, options)
            
            if not compile_result.success:
                return compile_result
            
            # 获取可执行文件路径
            if not compile_result.executable_files:
                return CppCheckResult(
                    success=False,
                    check_type=CppCheckType.EXECUTE,
                    errors=["没有找到可执行文件"],
                    execution_time=time.time() - start_time
                )
            
            executable_file = compile_result.executable_files[0]
            
            # 获取容器实例
            container_id = compile_result.container_id
            container = self.container_manager.client.containers.get(container_id)
            
            # 执行可执行文件
            exit_code, stdout, stderr = self.container_manager._execute_command(
                container, 
                [executable_file], 
                timeout=options.timeout
            )
            
            execution_time = time.time() - start_time
            
            # 解析结果
            success = exit_code == 0
            errors = self._parse_cpp_error(stderr.decode('utf-8')) if stderr else []
            output = stdout.decode('utf-8') if stdout else ""
            
            return CppCheckResult(
                success=success,
                check_type=CppCheckType.EXECUTE,
                errors=errors,
                output=output,
                execution_time=execution_time,
                executable_files=compile_result.executable_files,
                container_id=container_id,
                compiler_type=compile_result.compiler_type,
                standard=compile_result.standard
            )
            
        except ContainerTimeoutError as e:
            logger.error(f"C/C++代码执行超时: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.EXECUTE,
                errors=[f"代码执行超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"C/C++代码执行失败: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.EXECUTE,
                errors=[f"代码执行失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def build_with_make(self,
                             makefile_content: str,
                             options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        使用Make构建系统编译项目
        
        Args:
            makefile_content: Makefile内容
            options: 执行选项
            
        Returns:
            CppCheckResult: 构建结果
        """
        start_time = time.time()
        options = options or CppExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时Makefile
            async with self.file_manager.temporary_file_context(
                makefile_content.encode('utf-8'),
                "Makefile"
            ) as makefile_id:
                
                # 设置容器配置
                image = self._get_cpp_image(options.compiler_type or CppCompilerType.GCC)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("cpp")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="cpp",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建Make命令
                command = ["make"]
                if options.make_targets:
                    command.extend(options.make_targets)
                
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
                
                # 解析目标和错误
                targets, errors = self._parse_make_output(output + error_output)
                
                # 收集生成的可执行文件
                executable_files = []
                if success:
                    # 尝试从Makefile中提取可执行文件名
                    executable_match = re.search(r'([a-zA-Z0-9_]+)\s*:', makefile_content)
                    if executable_match:
                        executable_files.append(executable_match.group(1))
                
                return CppCheckResult(
                    success=success,
                    check_type=CppCheckType.MAKE_BUILD,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    executable_files=executable_files,
                    container_id=container_id,
                    compiler_type=options.compiler_type or CppCompilerType.GCC
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"Make构建超时: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.MAKE_BUILD,
                errors=[f"Make构建超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"Make构建失败: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.MAKE_BUILD,
                errors=[f"Make构建失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def build_with_cmake(self,
                              cmakefile_content: str,
                              source_files: List[str] = None,
                              options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        使用CMake构建系统编译项目
        
        Args:
            cmakefile_content: CMakeLists.txt内容
            source_files: 源文件列表（可选）
            options: 执行选项
            
        Returns:
            CppCheckResult: 构建结果
        """
        start_time = time.time()
        options = options or CppExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 创建临时CMakeLists.txt
            async with self.file_manager.temporary_file_context(
                cmakefile_content.encode('utf-8'),
                "CMakeLists.txt"
            ) as cmakefile_id:
                
                # 如果提供了源文件，也创建它们
                source_file_ids = []
                if source_files:
                    for i, source_code in enumerate(source_files):
                        # 假设源文件名格式为 "source{i+1}.cpp"
                        filename = f"source{i+1}.cpp"
                        async with self.file_manager.temporary_file_context(
                            source_code.encode('utf-8'),
                            filename
                        ) as file_id:
                            source_file_ids.append(file_id)
                
                # 设置容器配置
                image = self._get_cpp_image(options.compiler_type or CppCompilerType.GCC)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                container_id = self.container_manager.get_container_for_language("cpp")
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language="cpp",
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 创建构建目录
                self.container_manager._execute_command(
                    container,
                    ["mkdir", "-p", "build"],
                    timeout=10
                )
                
                # 运行CMake配置
                cmake_config_command = ["cmake", ".."]
                if options.cmake_args:
                    cmake_config_command.extend(options.cmake_args)
                
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container,
                    cmake_config_command,
                    timeout=options.timeout,
                    workdir="build"
                )
                
                config_output = stdout.decode('utf-8') if stdout else ""
                config_error = stderr.decode('utf-8') if stderr else ""
                
                # 如果配置失败，返回结果
                if exit_code != 0:
                    configs, errors = self._parse_cmake_output(config_output + config_error)
                    return CppCheckResult(
                        success=False,
                        check_type=CppCheckType.CMAKE_BUILD,
                        errors=errors,
                        output=config_output,
                        execution_time=time.time() - start_time,
                        container_id=container_id,
                        compiler_type=options.compiler_type or CppCompilerType.GCC
                    )
                
                # 运行CMake构建
                exit_code, stdout, stderr = self.container_manager._execute_command(
                    container,
                    ["cmake", "--build", "."],
                    timeout=options.timeout,
                    workdir="build"
                )
                
                execution_time = time.time() - start_time
                
                # 解析结果
                success = exit_code == 0
                output = config_output + (stdout.decode('utf-8') if stdout else "")
                error_output = stderr.decode('utf-8') if stderr else ""
                
                # 解析配置和错误
                configs, errors = self._parse_cmake_output(output + error_output)
                
                # 收集生成的可执行文件
                executable_files = []
                if success:
                    # 尝试从CMakeLists.txt中提取可执行文件名
                    executable_match = re.search(r'add_executable\s*\(\s*([^\s)]+)', cmakefile_content)
                    if executable_match:
                        executable_files.append(f"build/{executable_match.group(1)}")
                
                return CppCheckResult(
                    success=success,
                    check_type=CppCheckType.CMAKE_BUILD,
                    errors=errors,
                    output=output,
                    execution_time=execution_time,
                    executable_files=executable_files,
                    container_id=container_id,
                    compiler_type=options.compiler_type or CppCompilerType.GCC
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"CMake构建超时: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.CMAKE_BUILD,
                errors=[f"CMake构建超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"CMake构建失败: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.CMAKE_BUILD,
                errors=[f"CMake构建失败: {e}"],
                execution_time=time.time() - start_time
            )
    
    async def check_dependencies(self,
                               code: str,
                               filename: str = "main.cpp",
                               options: Optional[CppExecutionOptions] = None) -> CppCheckResult:
        """
        检查C/C++代码的依赖
        
        Args:
            code: C/C++代码
            filename: 文件名
            options: 执行选项
            
        Returns:
            CppCheckResult: 检查结果
        """
        start_time = time.time()
        options = options or CppExecutionOptions()
        
        # 确保清理任务已启动
        self._ensure_cleanup_tasks()
        
        try:
            # 检测编译器类型
            compiler_type = options.compiler_type or self._detect_language_from_file(filename)
            
            # 创建临时文件
            async with self.file_manager.temporary_file_context(
                code.encode('utf-8'),
                filename
            ) as file_id:
                
                # 设置容器配置
                image = self._get_cpp_image(compiler_type)
                resource_limits = ResourceLimits(
                    memory=options.memory_limit,
                    cpu_quota=options.cpu_limit
                )
                
                # 获取或创建容器
                language = "cpp" if compiler_type in [CppCompilerType.GPP, CppCompilerType.CLANGPP] else "c"
                container_id = self.container_manager.get_container_for_language(language)
                if not container_id:
                    container_id = self.container_manager.create_and_start_container(
                        language=language,
                        custom_image=image,
                        custom_resource_limits=resource_limits
                    )
                
                # 获取容器实例
                container = self.container_manager.client.containers.get(container_id)
                
                # 构建依赖检查命令
                file_path = f"{options.working_directory}/{file_id}"
                command = [self.COMPILER_COMMANDS[compiler_type]]
                
                # 添加编译标准
                if options.standard:
                    if "c++" in options.standard.value:
                        command.append(f"-std={options.standard.value}")
                    else:
                        command.append(f"-std={options.standard.value}")
                
                # 添加依赖检查标志
                command.extend(["-M", "-MF", "-"])
                
                # 添加头文件路径
                for path in options.include_paths:
                    command.extend(["-I", path])
                
                # 添加源文件
                command.append(file_path)
                
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
                
                # 提取依赖文件
                dependencies = []
                if success:
                    # 解析Make格式的依赖
                    lines = output.split('\n')
                    for line in lines:
                        # 跳过目标行（通常以冒号结尾）
                        if ':' in line:
                            # 获取依赖文件
                            deps = line.split(':')[1].strip()
                            for dep in deps.split():
                                dep = dep.strip()
                                if dep and not dep.startswith('/usr'):
                                    dependencies.append(dep)
                
                return CppCheckResult(
                    success=success,
                    check_type=CppCheckType.DEPENDENCIES,
                    errors=[error_output] if error_output else [],
                    output=output,
                    execution_time=execution_time,
                    dependencies=dependencies,
                    container_id=container_id,
                    compiler_type=compiler_type
                )
                
        except ContainerTimeoutError as e:
            logger.error(f"C/C++依赖检查超时: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.DEPENDENCIES,
                errors=[f"依赖检查超时: {e}"],
                execution_time=time.time() - start_time
            )
        except (ContainerExecutionError, FileManagerError) as e:
            logger.error(f"C/C++依赖检查失败: {e}")
            return CppCheckResult(
                success=False,
                check_type=CppCheckType.DEPENDENCIES,
                errors=[f"依赖检查失败: {e}"],
                execution_time=time.time() - start_time
            )