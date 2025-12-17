"""
Go编译器简单测试

简化版的Go编译器测试，确保基本功能正常工作。
"""

import pytest
import time

from app.docker.compilers.go_compiler import (
    GoCompiler,
    GoCheckType,
    GoExecutionOptions,
    GoCheckResult
)


class TestGoCompilerSimple:
    """Go编译器简单测试类"""
    
    def test_go_compiler_init(self):
        """测试Go编译器初始化"""
        try:
            compiler = GoCompiler()
            assert compiler is not None
            assert compiler.container_manager is not None
            assert compiler.file_manager is not None
            assert compiler._cleanup_tasks_started is False
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_get_go_image(self):
        """测试获取Go镜像"""
        try:
            compiler = GoCompiler()
            
            # 测试有效版本
            assert compiler._get_go_image("1.21") == "golang:1.21-alpine"
            assert compiler._get_go_image("1.20") == "golang:1.20-alpine"
            assert compiler._get_go_image("1.19") == "golang:1.19-alpine"
            
            # 测试无效版本（应该返回默认版本）
            assert compiler._get_go_image("invalid") == "golang:1.21-alpine"
            assert compiler._get_go_image("999") == "golang:1.21-alpine"
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_parse_go_error(self):
        """测试Go错误解析"""
        try:
            compiler = GoCompiler()
            
            # 测试常见错误
            error1 = "main.go:5:2: missing return statement"
            errors = compiler._parse_go_error(error1)
            assert len(errors) > 0
            assert "missing" in errors[0] and "return" in errors[0]
            
            # 测试多行错误
            error2 = "main.go:3:1: expected 'package'\nmain.go:4:1: expected declaration"
            errors = compiler._parse_go_error(error2)
            assert len(errors) > 0
            assert any("package" in error for error in errors)
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_parse_go_mod_output(self):
        """测试go mod输出解析"""
        try:
            compiler = GoCompiler()
            
            # 测试下载输出
            output1 = "go: downloading github.com/gin-gonic/gin v1.9.0"
            dependencies, errors = compiler._parse_go_mod_output(output1)
            assert len(dependencies) > 0
            assert "github.com/gin-gonic/gin" in dependencies[0]
            assert len(errors) == 0
            
            # 测试错误输出
            output2 = "go: errors parsing go.mod"
            dependencies, errors = compiler._parse_go_mod_output(output2)
            assert len(dependencies) == 0
            assert len(errors) > 0
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_parse_go_test_output(self):
        """测试go test输出解析"""
        try:
            compiler = GoCompiler()
            
            # 测试成功测试输出
            output1 = """=== RUN   TestAddition
--- PASS: TestAddition (0.00s)
PASS
ok      example 0.002s"""
            
            test_results = compiler._parse_go_test_output(output1)
            assert len(test_results) > 0
            assert test_results[0]["name"] == "TestAddition"
            assert test_results[0]["status"] == "passed"
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_is_go_module(self):
        """测试Go模块检测"""
        try:
            compiler = GoCompiler()
            
            # 测试包含模块声明的代码
            code1 = "module example.com/myapp\n\ngo 1.21"
            assert compiler._is_go_module(code1) is True
            
            # 测试不包含模块声明的代码
            code2 = "package main\n\nfunc main() {}"
            assert compiler._is_go_module(code2) is False
            
            # 测试包含go.mod的代码
            code3 = "// This is a comment about go.mod file"
            assert compiler._is_go_module(code3) is True
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_extract_go_version_from_code(self):
        """测试从代码提取Go版本"""
        try:
            compiler = GoCompiler()
            
            # 测试泛型代码
            code1 = "package main\n\ntype Stack[T any] []T"
            version = compiler._extract_go_version_from_code(code1)
            assert version >= "1.18"
            
            # 测试嵌入代码
            code2 = "package main\n\nimport \"embed\""
            version = compiler._extract_go_version_from_code(code2)
            assert version == "1.16" or version == "1.17"
            
            # 测试简单代码
            code3 = "package main\n\nfunc main() {}"
            version = compiler._extract_go_version_from_code(code3)
            assert version == "1.18"  # 默认版本
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_suggest_fix_for_error(self):
        """测试错误修复建议"""
        try:
            compiler = GoCompiler()
            
            # 测试常见错误
            error1 = "missing return statement"
            suggestion = compiler._suggest_fix_for_error(error1)
            assert "返回语句" in suggestion
            
            error2 = "undefined: variable"
            suggestion = compiler._suggest_fix_for_error(error2)
            assert "未定义" in suggestion
            
            error3 = "cannot use x (type int) as type string"
            suggestion = compiler._suggest_fix_for_error(error3)
            assert "类型不匹配" in suggestion
            
            # 测试未知错误
            error4 = "some unknown error"
            suggestion = compiler._suggest_fix_for_error(error4)
            assert suggestion is None
        except Exception as e:
            # 如果Docker未安装或未运行，则跳过此测试
            if "docker" in str(e).lower() or "error while fetching server api version" in str(e).lower():
                pytest.skip("Docker not available, skipping test")
            else:
                raise
    
    def test_go_execution_options(self):
        """测试Go执行选项"""
        options = GoExecutionOptions(
            timeout=60,
            go_version="1.20",
            memory_limit="512m",
            cpu_limit=75000,
            module_name="example.com/test"
        )
        
        assert options.timeout == 60
        assert options.go_version == "1.20"
        assert options.memory_limit == "512m"
        assert options.cpu_limit == 75000
        assert options.module_name == "example.com/test"
        assert options.race_detector is False
        assert options.verbose is False
    
    def test_go_check_result(self):
        """测试Go检查结果"""
        result = GoCheckResult(
            success=True,
            check_type=GoCheckType.SYNTAX,
            errors=[],
            warnings=["warning1"],
            output="test output",
            execution_time=1.5,
            dependencies=["dep1", "dep2"],
            go_version="1.21",
            container_id="test-container"
        )
        
        # 测试转换为字典
        result_dict = result.to_dict()
        assert result_dict["success"] is True
        assert result_dict["check_type"] == "syntax"
        assert len(result_dict["errors"]) == 0
        assert len(result_dict["warnings"]) == 1
        assert result_dict["output"] == "test output"
        assert result_dict["execution_time"] == 1.5
        assert len(result_dict["dependencies"]) == 2
        assert result_dict["go_version"] == "1.21"
        assert result_dict["container_id"] == "test-container"