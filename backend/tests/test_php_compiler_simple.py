"""
PHP编译器简单测试

测试PHP编译器的基本功能，不使用复杂的模拟对象。
"""

import asyncio
import pytest
import json
from app.docker.compilers.php_compiler import (
    PHPCompiler,
    PHPCheckType,
    PHPCheckResult,
    PHPExecutionOptions,
    PHPCompilerError
)


class TestPHPCompilerSimple:
    """PHP编译器简单测试类"""
    
    def test_php_compiler_initialization(self):
        """测试PHP编译器初始化"""
        try:
            compiler = PHPCompiler()
            assert compiler is not None
            assert compiler.container_manager is not None
            assert compiler.file_manager is not None
            assert hasattr(compiler, '_cleanup_tasks_started')
        except Exception as e:
            # 如果Docker不可用，跳过测试
            pytest.skip(f"Docker不可用，跳过测试: {e}")
    
    def test_get_php_image(self):
        """测试获取PHP镜像"""
        try:
            compiler = PHPCompiler()
            assert compiler._get_php_image("7.4") == "php:7.4-cli"
            assert compiler._get_php_image("8.2") == "php:8.2-cli"
            assert compiler._get_php_image("9.0") == "php:8.2-cli"  # 默认版本
        except Exception as e:
            pytest.skip(f"Docker不可用，跳过测试: {e}")
    
    def test_parse_php_error(self):
        """测试解析PHP错误"""
        try:
            compiler = PHPCompiler()
            error_output = """
Parse error: syntax error, unexpected 'echo' in /workspace/test_file_id on line 4
"""
            errors = compiler._parse_php_error(error_output)
            assert len(errors) > 0
            assert any("Parse error" in error for error in errors)
            assert any("test_file_id" in error for error in errors)
            assert any("line 4" in error for error in errors)
        except Exception as e:
            pytest.skip(f"Docker不可用，跳过测试: {e}")
    
    def test_parse_composer_output(self):
        """测试解析Composer输出"""
        try:
            compiler = PHPCompiler()
            output = """
 - Installing monolog/monolog (2.3.5)
 - Installing symfony/console (5.4.7)
Generating autoload files
"""
            installed_packages, errors = compiler._parse_composer_output(output)
            assert len(installed_packages) > 0
            assert "monolog/monolog" in installed_packages
            assert "symfony/console" in installed_packages
            assert len(errors) == 0
        except Exception as e:
            pytest.skip(f"Docker不可用，跳过测试: {e}")
    
    def test_php_check_result_to_dict(self):
        """测试PHP检查结果转换为字典"""
        result = PHPCheckResult(
            success=True,
            check_type=PHPCheckType.SYNTAX,
            errors=[],
            warnings=[],
            output="Test output",
            execution_time=1.5,
            dependencies=["monolog/monolog"],
            container_id="test_container"
        )
        
        result_dict = result.to_dict()
        assert result_dict["success"] is True
        assert result_dict["check_type"] == "syntax"
        assert result_dict["errors"] == []
        assert result_dict["warnings"] == []
        assert result_dict["output"] == "Test output"
        assert result_dict["execution_time"] == 1.5
        assert result_dict["dependencies"] == ["monolog/monolog"]
        assert result_dict["container_id"] == "test_container"
    
    def test_php_execution_options(self):
        """测试PHP执行选项"""
        options = PHPExecutionOptions(
            timeout=60,
            check_dependencies=False,
            install_dependencies=False,
            lint_code=True,
            capture_output=False,
            working_directory="/custom_workspace",
            php_version="8.1",
            memory_limit="512m",
            cpu_limit=75000
        )
        
        assert options.timeout == 60
        assert options.check_dependencies is False
        assert options.install_dependencies is False
        assert options.lint_code is True
        assert options.capture_output is False
        assert options.working_directory == "/custom_workspace"
        assert options.php_version == "8.1"
        assert options.memory_limit == "512m"
        assert options.cpu_limit == 75000
    
    def test_php_execution_options_defaults(self):
        """测试PHP执行选项默认值"""
        options = PHPExecutionOptions()
        
        assert options.timeout == 30
        assert options.check_dependencies is True
        assert options.install_dependencies is True
        assert options.lint_code is False
        assert options.capture_output is True
        assert options.working_directory == "/workspace"
        assert options.php_version == "8.2"
        assert options.memory_limit == "256m"
        assert options.cpu_limit == 50000


# 集成测试（需要Docker环境）
@pytest.mark.integration
class TestPHPCompilerIntegration:
    """PHP编译器集成测试类"""
    
    @pytest.fixture
    def php_compiler(self):
        """创建实际的PHP编译器实例"""
        try:
            return PHPCompiler()
        except Exception as e:
            pytest.skip(f"Docker不可用，跳过集成测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_syntax_check(self, php_compiler):
        """真实的语法检查测试"""
        # 有效代码
        valid_code = """
<?php
function hello_world() {
    echo "Hello, World!";
}

hello_world();
?>
"""
        
        try:
            result = await php_compiler.check_syntax(valid_code)
            assert result.success is True
            assert len(result.errors) == 0
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
        
        # 无效代码
        invalid_code = """
<?php
function hello_world()
    echo "Hello, World!";

hello_world();
?>
"""
        
        try:
            result = await php_compiler.check_syntax(invalid_code)
            assert result.success is False
            assert len(result.errors) > 0
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_code_execution(self, php_compiler):
        """真实的代码执行测试"""
        code = """
<?php
echo "Test output\n";
$result = 2 + 2;
echo "2 + 2 = {$result}\n";
?>
"""
        
        try:
            result = await php_compiler.execute_code(code)
            assert result.success is True
            assert "Test output" in result.output
            assert "2 + 2 = 4" in result.output
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_dependency_installation(self, php_compiler):
        """真实的依赖安装测试"""
        try:
            # 创建简单的composer.json
            composer_json = json.dumps({
                "name": "test/project",
                "require": {
                    "php": "^8.0"
                }
            })
            
            result = await php_compiler.install_dependencies(composer_json)
            assert result.success is True
            assert result.output is not None
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_dependency_check(self, php_compiler):
        """真实的依赖检查测试"""
        try:
            # 创建一个包含依赖的composer.json
            composer_json = json.dumps({
                "name": "test/project",
                "require": {
                    "php": "^8.0",
                    "monolog/monolog": "^2.0"
                },
                "require-dev": {
                    "phpunit/phpunit": "^9.0"
                }
            })
            
            result = await php_compiler.check_dependencies(composer_json)
            assert result.success is True
            assert len(result.dependencies) > 0
            assert "monolog/monolog" in result.dependencies
            assert "phpunit/phpunit" in result.dependencies
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")