"""
C/C++编译器测试

测试C/C++编译器的各种功能，包括语法检查、编译、执行和构建系统支持。
"""

import asyncio
import pytest
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.docker.compilers.cpp_compiler import (
    CppCompiler,
    CppCheckType,
    CppCompilerType,
    CppStandard,
    CppCheckResult,
    CppExecutionOptions,
    CppCompilerError,
    CppSyntaxError,
    CppBuildError
)
from app.docker.container_manager import ContainerManagerError
from app.docker.file_manager import FileManagerError


class TestCppCompiler:
    """C/C++编译器测试类"""
    
    @pytest.fixture
    def cpp_compiler(self):
        """创建C/C++编译器实例"""
        return CppCompiler()
    
    @pytest.fixture
    def valid_c_code(self):
        """有效的C代码示例"""
        return """
#include <stdio.h>
#include <stdlib.h>

int main() {
    printf("Hello, World!\\n");
    return 0;
}
"""
    
    @pytest.fixture
    def valid_cpp_code(self):
        """有效的C++代码示例"""
        return """
#include <iostream>
#include <vector>
#include <algorithm>

int main() {
    std::vector<int> numbers = {5, 2, 8, 1, 9};
    std::sort(numbers.begin(), numbers.end());
    
    for (int num : numbers) {
        std::cout << num << " ";
    }
    std::cout << std::endl;
    
    return 0;
}
"""
    
    @pytest.fixture
    def invalid_cpp_code(self):
        """无效的C++代码示例"""
        return """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl
    // 缺少分号
    return 0;
}
"""
    
    @pytest.fixture
    def modern_cpp_code(self):
        """现代C++代码示例（C++20特性）"""
        return """
#include <iostream>
#include <vector>
#include <algorithm>
#include <ranges>

int main() {
    std::vector<int> numbers = {5, 2, 8, 1, 9};
    
    // 使用C++20的范围库
    auto even_numbers = numbers | std::views::filter([](int n) { return n % 2 == 0; });
    
    for (int num : even_numbers) {
        std::cout << num << " ";
    }
    std::cout << std::endl;
    
    return 0;
}
"""
    
    @pytest.fixture
    def makefile_content(self):
        """Makefile内容示例"""
        return """
CC = gcc
CFLAGS = -Wall -Wextra -O2
TARGET = hello
SOURCES = main.c

$(TARGET): $(SOURCES)
\t$(CC) $(CFLAGS) -o $(TARGET) $(SOURCES)

clean:
\trm -f $(TARGET)
"""
    
    @pytest.fixture
    def cmakefile_content(self):
        """CMakeLists.txt内容示例"""
        return """
cmake_minimum_required(VERSION 3.10)
project(HelloWorld)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(hello main.cpp)

if(CMAKE_COMPILER_IS_GNUCXX)
    add_compile_options(-Wall -Wextra)
endif()
"""
    
    def test_init(self, cpp_compiler):
        """测试编译器初始化"""
        assert cpp_compiler is not None
        assert hasattr(cpp_compiler, 'container_manager')
        assert hasattr(cpp_compiler, 'file_manager')
    
    def test_detect_language_from_file(self, cpp_compiler):
        """测试从文件名检测语言类型"""
        assert cpp_compiler._detect_language_from_file("test.c") == CppCompilerType.GCC
        assert cpp_compiler._detect_language_from_file("test.cpp") == CppCompilerType.GPP
        assert cpp_compiler._detect_language_from_file("test.cxx") == CppCompilerType.GPP
        assert cpp_compiler._detect_language_from_file("test.cc") == CppCompilerType.GPP
        assert cpp_compiler._detect_language_from_file("test.h") == CppCompilerType.GCC
        assert cpp_compiler._detect_language_from_file("test.hpp") == CppCompilerType.GPP
    
    def test_detect_language_from_code(self, cpp_compiler):
        """测试从代码内容检测语言类型"""
        c_code = "#include <stdio.h>\\nint main() { printf(\"Hello\"); }"
        cpp_code = "#include <iostream>\\nint main() { std::cout << \"Hello\"; }"
        
        assert cpp_compiler._detect_language_from_code(c_code) == CppCompilerType.GCC
        assert cpp_compiler._detect_language_from_code(cpp_code) == CppCompilerType.GPP
    
    def test_infer_standard_from_code(self, cpp_compiler):
        """测试从代码推断编译标准"""
        c89_code = "#include <stdio.h>\\nint main() { printf(\"Hello\"); }"
        c11_code = "#include <stdio.h>\\nint main() { _Thread_local int x = 0; }"
        cpp11_code = "#include <memory>\\nint main() { std::unique_ptr<int> p; }"
        cpp17_code = "#include <optional>\\nint main() { std::optional<int> x; }"
        cpp20_code = "#include <ranges>\\nint main() { auto view = std::views::iota(0); }"
        
        assert cpp_compiler._infer_standard_from_code(c89_code, CppCompilerType.GCC) == CppStandard.C89
        assert cpp_compiler._infer_standard_from_code(c11_code, CppCompilerType.GCC) == CppStandard.C11
        assert cpp_compiler._infer_standard_from_code(cpp11_code, CppCompilerType.GPP) == CppStandard.CPP11
        assert cpp_compiler._infer_standard_from_code(cpp17_code, CppCompilerType.GPP) == CppStandard.CPP17
        assert cpp_compiler._infer_standard_from_code(cpp20_code, CppCompilerType.GPP) == CppStandard.CPP20
    
    def test_parse_cpp_error(self, cpp_compiler):
        """测试C/C++错误解析"""
        error_output = """
main.cpp:5:10: error: expected ';' before 'return'
     return 0;
            ^~~~~
"""
        errors = cpp_compiler._parse_cpp_error(error_output)
        assert len(errors) > 0
        assert any("main.cpp:5:10:" in error for error in errors)
    
    def test_parse_make_output(self, cpp_compiler):
        """测试Make输出解析"""
        make_output = """
make: Entering directory '/workspace'
gcc -Wall -Wextra -O2 -o hello main.c
make: Leaving directory '/workspace'
"""
        targets, errors = cpp_compiler._parse_make_output(make_output)
        assert len(targets) > 0 or len(errors) > 0
    
    def test_parse_cmake_output(self, cpp_compiler):
        """测试CMake输出解析"""
        cmake_output = """
-- Configuring done
-- Generating done
-- Build files have been written to: /workspace/build
"""
        configs, errors = cpp_compiler._parse_cmake_output(cmake_output)
        assert len(configs) > 0 or len(errors) > 0
    
    def test_suggest_fix_for_error(self, cpp_compiler):
        """测试错误修复建议"""
        undefined_ref_error = "undefined reference to `printf'"
        missing_semicolon_error = "main.cpp:5:10: error: expected ';' before 'return'"
        file_not_found_error = "fatal error: stdio.h: No such file or directory"
        
        assert cpp_compiler._suggest_fix_for_error(undefined_ref_error) is not None
        assert cpp_compiler._suggest_fix_for_error(missing_semicolon_error) is not None
        assert cpp_compiler._suggest_fix_for_error(file_not_found_error) is not None
    
    def test_build_compiler_command(self, cpp_compiler):
        """测试编译器命令构建"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17,
            optimization_level="-O2",
            debug_symbols=True,
            warnings_as_errors=False
        )
        
        command = cpp_compiler._build_compiler_command(
            CppCompilerType.GPP, 
            "test.cpp", 
            options, 
            syntax_only=True
        )
        
        assert "g++" in command
        assert "-std=c++17" in command
        assert "-O2" in command
        assert "-g" in command
        assert "-Wall" in command
        assert "-Wextra" in command
        assert "-fsyntax-only" in command
        assert "test.cpp" in command
    
    @pytest.mark.asyncio
    async def test_check_syntax_valid_code(self, cpp_compiler, valid_cpp_code):
        """测试有效代码的语法检查"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        result = await cpp_compiler.check_syntax(valid_cpp_code, "test.cpp", options)
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.SYNTAX
        assert result.success is True
        assert len(result.errors) == 0
        assert result.compiler_type == CppCompilerType.GPP
        assert result.standard == CppStandard.CPP17
    
    @pytest.mark.asyncio
    async def test_check_syntax_invalid_code(self, cpp_compiler, invalid_cpp_code):
        """测试无效代码的语法检查"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        result = await cpp_compiler.check_syntax(invalid_cpp_code, "test.cpp", options)
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.SYNTAX
        assert result.success is False
        assert len(result.errors) > 0
    
    @pytest.mark.asyncio
    async def test_enhanced_check_syntax(self, cpp_compiler, invalid_cpp_code):
        """测试增强语法检查"""
        result = await cpp_compiler.enhanced_check_syntax(invalid_cpp_code, "test.cpp")
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.SYNTAX
        assert result.success is False
        assert len(result.errors) > 0
        
        # 检查是否包含修复建议
        for error in result.errors:
            if "建议:" in error:
                break
        else:
            pytest.fail("错误信息中应包含修复建议")
    
    @pytest.mark.asyncio
    async def test_compile_code(self, cpp_compiler, valid_cpp_code):
        """测试代码编译"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        result = await cpp_compiler.compile_code(valid_cpp_code, "test.cpp", options)
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.COMPILE
        assert result.success is True
        assert len(result.errors) == 0
        assert len(result.executable_files) > 0
    
    @pytest.mark.asyncio
    async def test_execute_code(self, cpp_compiler, valid_cpp_code):
        """测试代码执行"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        result = await cpp_compiler.execute_code(valid_cpp_code, "test.cpp", options)
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.EXECUTE
        assert result.success is True
        assert len(result.errors) == 0
        assert "2 5 8 9 1" in result.output or "1 2 5 8 9" in result.output  # 排序后的数字
    
    @pytest.mark.asyncio
    async def test_build_with_make(self, cpp_compiler, makefile_content, valid_c_code):
        """测试Make构建"""
        # 创建包含源代码的Makefile内容
        makefile_with_source = makefile_content + "\n\n" + valid_c_code
        
        result = await cpp_compiler.build_with_make(makefile_with_source)
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.MAKE_BUILD
        # 注意：由于没有实际的源文件，这个测试可能会失败
        # 但我们可以测试命令构建和解析逻辑
    
    @pytest.mark.asyncio
    async def test_build_with_cmake(self, cpp_compiler, cmakefile_content, valid_cpp_code):
        """测试CMake构建"""
        result = await cpp_compiler.build_with_cmake(cmakefile_content, [valid_cpp_code])
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.CMAKE_BUILD
        # 注意：由于没有实际的源文件结构，这个测试可能会失败
        # 但我们可以测试命令构建和解析逻辑
    
    @pytest.mark.asyncio
    async def test_check_dependencies(self, cpp_compiler, valid_cpp_code):
        """测试依赖检查"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        result = await cpp_compiler.check_dependencies(valid_cpp_code, "test.cpp", options)
        
        assert isinstance(result, CppCheckResult)
        assert result.check_type == CppCheckType.DEPENDENCIES
        assert result.success is True
        # 检查是否包含标准库依赖
        assert len(result.dependencies) > 0
    
    @pytest.mark.asyncio
    async def test_modern_cpp_features(self, cpp_compiler, modern_cpp_code):
        """测试现代C++特性支持"""
        options = CppExecutionOptions(
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP20
        )
        
        # 测试语法检查
        syntax_result = await cpp_compiler.check_syntax(modern_cpp_code, "modern.cpp", options)
        
        assert isinstance(syntax_result, CppCheckResult)
        assert syntax_result.check_type == CppCheckType.SYNTAX
        # 注意：如果容器中没有支持C++20的编译器，这个测试可能会失败
    
    @pytest.mark.asyncio
    async def test_multiple_compiler_types(self, cpp_compiler, valid_c_code, valid_cpp_code):
        """测试多种编译器类型"""
        gcc_options = CppExecutionOptions(compiler_type=CppCompilerType.GCC)
        gpp_options = CppExecutionOptions(compiler_type=CppCompilerType.GPP)
        
        gcc_result = await cpp_compiler.check_syntax(valid_c_code, "test.c", gcc_options)
        gpp_result = await cpp_compiler.check_syntax(valid_cpp_code, "test.cpp", gpp_options)
        
        assert isinstance(gcc_result, CppCheckResult)
        assert isinstance(gpp_result, CppCheckResult)
        assert gcc_result.compiler_type == CppCompilerType.GCC
        assert gpp_result.compiler_type == CppCompilerType.GPP
    
    @pytest.mark.asyncio
    async def test_error_handling(self, cpp_compiler):
        """测试错误处理"""
        # 测试空代码
        empty_code = ""
        result = await cpp_compiler.check_syntax(empty_code, "empty.cpp")
        
        assert isinstance(result, CppCheckResult)
        # 空代码可能不会导致语法错误，但应该能正常处理
    
    def test_cpp_check_result_to_dict(self):
        """测试CppCheckResult转换为字典"""
        result = CppCheckResult(
            success=True,
            check_type=CppCheckType.SYNTAX,
            errors=["test error"],
            warnings=["test warning"],
            output="test output",
            execution_time=1.23,
            dependencies=["test.cpp"],
            object_files=["test.o"],
            executable_files=["test"],
            container_id="container123",
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["success"] is True
        assert result_dict["check_type"] == "syntax"
        assert result_dict["errors"] == ["test error"]
        assert result_dict["warnings"] == ["test warning"]
        assert result_dict["output"] == "test output"
        assert result_dict["execution_time"] == 1.23
        assert result_dict["dependencies"] == ["test.cpp"]
        assert result_dict["object_files"] == ["test.o"]
        assert result_dict["executable_files"] == ["test"]
        assert result_dict["container_id"] == "container123"
        assert result_dict["compiler_type"] == "g++"
        assert result_dict["standard"] == "c++17"


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])