"""
Go编译器测试

测试Go编译器的各种功能，包括语法检查、编译、执行和模块管理。
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from app.docker.compilers.go_compiler import (
    GoCompiler,
    GoCheckType,
    GoExecutionOptions,
    GoCheckResult,
    GoCompilerError,
    GoSyntaxError,
    GoModuleError,
    GoTestError
)


@pytest.fixture
def mock_container_manager():
    """模拟容器管理器"""
    manager = Mock()
    manager.get_container_for_language = Mock(return_value="test-container-id")
    manager.create_and_start_container = Mock(return_value="test-container-id")
    manager.client = Mock()
    manager._pool = Mock()
    manager._pool.start_cleanup_task = Mock()
    return manager


@pytest.fixture
def mock_file_manager():
    """模拟文件管理器"""
    manager = Mock()
    manager.temporary_file_context = AsyncMock()
    manager.setup_file_mounts = Mock(return_value={})
    manager.create_secure_temp_file = Mock(return_value="test-file-id")
    return manager


@pytest.fixture
def go_compiler(mock_container_manager, mock_file_manager):
    """创建Go编译器实例"""
    return GoCompiler(
        container_manager=mock_container_manager,
        file_manager=mock_file_manager
    )


class TestGoCompiler:
    """Go编译器测试类"""
    
    @pytest.mark.asyncio
    async def test_init(self, go_compiler):
        """测试编译器初始化"""
        assert go_compiler.container_manager is not None
        assert go_compiler.file_manager is not None
        assert go_compiler._cleanup_tasks_started is False
    
    @pytest.mark.asyncio
    async def test_ensure_cleanup_tasks(self, go_compiler):
        """测试清理任务启动"""
        go_compiler._ensure_cleanup_tasks()
        assert go_compiler._cleanup_tasks_started is True
    
    def test_get_go_image(self, go_compiler):
        """测试获取Go镜像"""
        assert go_compiler._get_go_image("1.21") == "golang:1.21-alpine"
        assert go_compiler._get_go_image("1.20") == "golang:1.20-alpine"
        assert go_compiler._get_go_image("invalid") == "golang:1.21-alpine"
    
    def test_parse_go_error(self, go_compiler):
        """测试Go错误解析"""
        error_output = "main.go:5:2: missing return statement"
        errors = go_compiler._parse_go_error(error_output)
        assert len(errors) > 0
        assert "missing return statement" in errors[0]
    
    def test_parse_go_mod_output(self, go_compiler):
        """测试go mod输出解析"""
        output = "go: downloading github.com/gin-gonic/gin v1.9.0"
        dependencies, errors = go_compiler._parse_go_mod_output(output)
        assert len(dependencies) > 0
        assert "github.com/gin-gonic/gin" in dependencies[0]
    
    def test_parse_go_test_output(self, go_compiler):
        """测试go test输出解析"""
        output = """=== RUN   TestAddition
--- PASS: TestAddition (0.00s)
PASS
ok      example 0.002s"""
        test_results = go_compiler._parse_go_test_output(output)
        assert len(test_results) > 0
        assert test_results[0]["name"] == "TestAddition"
        assert test_results[0]["status"] == "passed"
    
    def test_is_go_module(self, go_compiler):
        """测试Go模块检测"""
        code_with_module = "module example.com/myapp\n\ngo 1.21"
        assert go_compiler._is_go_module(code_with_module) is True
        
        code_without_module = "package main\n\nfunc main() {}"
        assert go_compiler._is_go_module(code_without_module) is False
    
    def test_extract_go_version_from_code(self, go_compiler):
        """测试从代码提取Go版本"""
        code_with_generics = "package main\n\ntype Stack[T any] []T"
        version = go_compiler._extract_go_version_from_code(code_with_generics)
        assert version >= "1.18"
        
        code_with_embed = "package main\n\nimport \"embed\""
        version = go_compiler._extract_go_version_from_code(code_with_embed)
        assert version == "1.16" or version == "1.17"
    
    def test_suggest_fix_for_error(self, go_compiler):
        """测试错误修复建议"""
        error_msg = "missing return statement"
        suggestion = go_compiler._suggest_fix_for_error(error_msg)
        assert "返回语句" in suggestion
        
        error_msg = "undefined: variable"
        suggestion = go_compiler._suggest_fix_for_error(error_msg)
        assert "未定义" in suggestion
    
    @pytest.mark.asyncio
    async def test_check_syntax_success(self, go_compiler):
        """测试成功语法检查"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (0, b"", b"")
        
        # 使用上下文管理器模拟
        async with go_compiler.file_manager.temporary_file_context() as file_id:
            pass
        
        # 设置模拟返回值
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "package main\n\nfunc main() {}"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.check_syntax(code, "main.go", options)
        
        assert result.success is True
        assert result.check_type == GoCheckType.SYNTAX
        assert result.errors == []
        assert result.go_version == "1.21"
    
    @pytest.mark.asyncio
    async def test_check_syntax_failure(self, go_compiler):
        """测试失败语法检查"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (1, b"", b"main.go:2:1: expected 'package'")
        
        # 使用上下文管理器模拟
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "invalid code"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.check_syntax(code, "main.go", options)
        
        assert result.success is False
        assert result.check_type == GoCheckType.SYNTAX
        assert len(result.errors) > 0
        assert "expected" in result.errors[0]
    
    @pytest.mark.asyncio
    async def test_compile_code_success(self, go_compiler):
        """测试成功代码编译"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (0, b"", b"")
        
        # 使用上下文管理器模拟
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "package main\n\nfunc main() {}"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.compile_code(code, "main.go", options)
        
        assert result.success is True
        assert result.check_type == GoCheckType.COMPILE
        assert result.errors == []
    
    @pytest.mark.asyncio
    async def test_execute_code_success(self, go_compiler):
        """测试成功代码执行"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (0, b"Hello, World!", b"")
        
        # 使用上下文管理器模拟
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "package main\n\nimport \"fmt\"\n\nfunc main() { fmt.Println(\"Hello, World!\") }"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.execute_code(code, "main.go", options)
        
        assert result.success is True
        assert result.check_type == GoCheckType.EXECUTE
        assert "Hello, World!" in result.output
    
    @pytest.mark.asyncio
    async def test_init_go_module_success(self, go_compiler):
        """测试成功Go模块初始化"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (0, b"go: creating new go.mod: module example.com/myapp", b"")
        
        result = await go_compiler.init_go_module("example.com/myapp", "1.21")
        
        assert result.success is True
        assert result.check_type == GoCheckType.MOD_INIT
        assert "go.mod" in result.output
    
    @pytest.mark.asyncio
    async def test_tidy_go_module_success(self, go_compiler):
        """测试成功Go模块整理"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (0, b"", b"")
        
        result = await go_compiler.tidy_go_module("1.21")
        
        assert result.success is True
        assert result.check_type == GoCheckType.MOD_TIDY
    
    @pytest.mark.asyncio
    async def test_download_go_module_success(self, go_compiler):
        """测试成功Go模块下载"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (0, b"go: downloading github.com/gin-gonic/gin v1.9.0", b"")
        
        modules = ["github.com/gin-gonic/gin@latest"]
        result = await go_compiler.download_go_module(modules, "1.21")
        
        assert result.success is True
        assert result.check_type == GoCheckType.MOD_DOWNLOAD
        assert len(result.dependencies) > 0
    
    @pytest.mark.asyncio
    async def test_run_go_tests_success(self, go_compiler):
        """测试成功Go测试运行"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        test_output = """=== RUN   TestAddition
--- PASS: TestAddition (0.00s)
PASS
ok      example 0.002s"""
        go_compiler.container_manager._execute_command.return_value = (0, test_output.encode(), b"")
        
        result = await go_compiler.run_go_tests("./...", "1.21")
        
        assert result.success is True
        assert result.check_type == GoCheckType.TEST
        assert len(result.test_results) > 0
        assert result.test_results[0]["status"] == "passed"
    
    @pytest.mark.asyncio
    async def test_run_go_tests_failure(self, go_compiler):
        """测试失败Go测试运行"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        test_output = """=== RUN   TestAddition
--- FAIL: TestAddition (0.00s)
FAIL
FAIL    example 0.002s"""
        go_compiler.container_manager._execute_command.return_value = (1, test_output.encode(), b"")
        
        result = await go_compiler.run_go_tests("./...", "1.21")
        
        assert result.success is False
        assert result.check_type == GoCheckType.TEST
        assert len(result.test_results) > 0
        assert result.test_results[0]["status"] == "failed"
    
    @pytest.mark.asyncio
    async def test_enhanced_check_syntax(self, go_compiler):
        """测试增强语法检查"""
        # 设置模拟
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.return_value = (1, b"", b"main.go:2:1: expected 'package'")
        
        # 使用上下文管理器模拟
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "invalid code"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.enhanced_check_syntax(code, "main.go", options)
        
        assert result.success is False
        assert result.check_type == GoCheckType.SYNTAX
        assert len(result.errors) > 0
        assert any("建议" in error for error in result.errors)  # 检查是否包含修复建议
    
    @pytest.mark.asyncio
    async def test_check_syntax_timeout(self, go_compiler):
        """测试语法检查超时"""
        # 设置模拟
        from app.docker.container_manager import ContainerTimeoutError
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.side_effect = ContainerTimeoutError("Timeout")
        
        # 使用上下文管理器模拟
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "package main\n\nfunc main() {}"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.check_syntax(code, "main.go", options)
        
        assert result.success is False
        assert result.check_type == GoCheckType.SYNTAX
        assert "超时" in result.errors[0]
    
    @pytest.mark.asyncio
    async def test_check_syntax_container_error(self, go_compiler):
        """测试容器错误"""
        # 设置模拟
        from app.docker.container_manager import ContainerExecutionError
        mock_container = Mock()
        go_compiler.container_manager.client.containers.get.return_value = mock_container
        go_compiler.container_manager._execute_command.side_effect = ContainerExecutionError("Container error")
        
        # 使用上下文管理器模拟
        go_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "package main\n\nfunc main() {}"
        options = GoExecutionOptions(go_version="1.21")
        
        result = await go_compiler.check_syntax(code, "main.go", options)
        
        assert result.success is False
        assert result.check_type == GoCheckType.SYNTAX
        assert "失败" in result.errors[0]
    
    def test_go_check_result_to_dict(self):
        """测试GoCheckResult转换为字典"""
        result = GoCheckResult(
            success=True,
            check_type=GoCheckType.SYNTAX,
            go_version="1.21",
            container_id="test-container-id",
            dependencies=["github.com/gin-gonic/gin"],
            test_results=[{"name": "TestAddition", "status": "passed"}]
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["success"] is True
        assert result_dict["check_type"] == "syntax"
        assert result_dict["go_version"] == "1.21"
        assert result_dict["container_id"] == "test-container-id"
        assert len(result_dict["dependencies"]) == 1
        assert len(result_dict["test_results"]) == 1
        assert result_dict["test_results"][0]["name"] == "TestAddition"