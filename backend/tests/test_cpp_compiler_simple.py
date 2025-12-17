"""
C/C++编译器简单测试

测试C/C++编译器的基本功能，不依赖Docker环境。
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.docker.compilers.cpp_compiler import (
    CppCompilerType,
    CppStandard,
    CppCheckResult,
    CppExecutionOptions
)


class TestCppCompilerSimple:
    """C/C++编译器简单测试类"""
    
    def test_cpp_compiler_type_enum(self):
        """测试C/C++编译器类型枚举"""
        assert CppCompilerType.GCC.value == "gcc"
        assert CppCompilerType.GPP.value == "g++"
        assert CppCompilerType.CLANG.value == "clang"
        assert CppCompilerType.CLANGPP.value == "clang++"
    
    def test_cpp_standard_enum(self):
        """测试C/C++标准枚举"""
        # C标准
        assert CppStandard.C89.value == "c89"
        assert CppStandard.C99.value == "c99"
        assert CppStandard.C11.value == "c11"
        assert CppStandard.C17.value == "c17"
        assert CppStandard.C23.value == "c23"
        
        # C++标准
        assert CppStandard.CPP98.value == "c++98"
        assert CppStandard.CPP03.value == "c++03"
        assert CppStandard.CPP11.value == "c++11"
        assert CppStandard.CPP14.value == "c++14"
        assert CppStandard.CPP17.value == "c++17"
        assert CppStandard.CPP20.value == "c++20"
        assert CppStandard.CPP23.value == "c++23"
    
    def test_cpp_execution_options(self):
        """测试C/C++执行选项"""
        options = CppExecutionOptions(
            timeout=60,
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17,
            optimization_level="-O2",
            debug_symbols=True
        )
        
        assert options.timeout == 60
        assert options.compiler_type == CppCompilerType.GPP
        assert options.standard == CppStandard.CPP17
        assert options.optimization_level == "-O2"
        assert options.debug_symbols is True
        assert options.warnings_as_errors is False
        assert options.memory_limit == "256m"
        assert options.cpu_limit == 50000
    
    def test_cpp_check_result(self):
        """测试C/C++检查结果"""
        result = CppCheckResult(
            success=True,
            check_type="syntax",
            errors=[],
            warnings=["test warning"],
            output="test output",
            execution_time=1.23,
            dependencies=["iostream"],
            object_files=["test.o"],
            executable_files=["test"],
            compiler_type=CppCompilerType.GPP,
            standard=CppStandard.CPP17
        )
        
        assert result.success is True
        assert result.check_type.value == "syntax"
        assert result.errors == []
        assert result.warnings == ["test warning"]
        assert result.output == "test output"
        assert result.execution_time == 1.23
        assert result.dependencies == ["iostream"]
        assert result.object_files == ["test.o"]
        assert result.executable_files == ["test"]
        assert result.compiler_type == CppCompilerType.GPP
        assert result.standard == CppStandard.CPP17
    
    def test_cpp_check_result_to_dict(self):
        """测试C/C++检查结果转换为字典"""
        result = CppCheckResult(
            success=True,
            check_type="syntax",
            errors=["test error"],
            warnings=["test warning"],
            output="test output",
            execution_time=1.23,
            dependencies=["iostream"],
            object_files=["test.o"],
            executable_files=["test"],
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
        assert result_dict["dependencies"] == ["iostream"]
        assert result_dict["object_files"] == ["test.o"]
        assert result_dict["executable_files"] == ["test"]
        assert result_dict["compiler_type"] == "g++"
        assert result_dict["standard"] == "c++17"


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])