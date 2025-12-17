"""
Rust编译器测试

测试Rust编译器的各种功能，包括语法检查、编译、执行和Cargo包管理。
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from app.docker.compilers.rust_compiler import (
    RustCompiler,
    RustCheckType,
    RustExecutionOptions,
    RustCheckResult,
    RustCompilerError,
    RustSyntaxError,
    RustCargoError,
    RustTestError
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
def rust_compiler(mock_container_manager, mock_file_manager):
    """创建Rust编译器实例"""
    return RustCompiler(
        container_manager=mock_container_manager,
        file_manager=mock_file_manager
    )


class TestRustCompiler:
    """Rust编译器测试类"""
    
    @pytest.mark.asyncio
    async def test_init(self, rust_compiler):
        """测试编译器初始化"""
        assert rust_compiler.container_manager is not None
        assert rust_compiler.file_manager is not None
        assert rust_compiler._cleanup_tasks_started is False
    
    @pytest.mark.asyncio
    async def test_ensure_cleanup_tasks(self, rust_compiler):
        """测试清理任务启动"""
        rust_compiler._ensure_cleanup_tasks()
        assert rust_compiler._cleanup_tasks_started is True
    
    def test_get_rust_image(self, rust_compiler):
        """测试获取Rust镜像"""
        assert rust_compiler._get_rust_image("1.75") == "rust:1.75-slim"
        assert rust_compiler._get_rust_image("1.74") == "rust:1.74-slim"
        assert rust_compiler._get_rust_image("invalid") == "rust:1.75-slim"
    
    def test_parse_rust_error(self, rust_compiler):
        """测试Rust错误解析"""
        error_output = "error[E0425]: cannot find value `x` in this scope"
        errors = rust_compiler._parse_rust_error(error_output)
        assert len(errors) > 0
        assert "cannot find value" in errors[0]
        
        error_output = "main.rs:5:1: 5:13: error[E0596]: cannot borrow `x` as mutable"
        errors = rust_compiler._parse_rust_error(error_output)
        assert len(errors) > 0
        assert "cannot borrow" in errors[0]
    
    def test_parse_rust_warnings(self, rust_compiler):
        """测试Rust警告解析"""
        output = "warning: unused variable: `x`\n--> src/main.rs:5:13\n  |\n5 |     let x = 42;\n  |     ^ help: if this is intentional, prefix it with an underscore: `_x`"
        warnings = rust_compiler._parse_rust_warnings(output)
        assert len(warnings) > 0
        assert "unused variable" in warnings[0]
    
    def test_parse_cargo_output(self, rust_compiler):
        """测试Cargo输出解析"""
        output = "Updating crates.io index\nDownloading serde v1.0.136\nCompiling serde_json v1.0.79"
        cargo_info = rust_compiler._parse_cargo_output(output)
        assert len(cargo_info["dependencies"]) > 0
        assert "serde v1.0.136" in cargo_info["dependencies"][0]
    
    def test_parse_test_output(self, rust_compiler):
        """测试测试输出解析"""
        output = """running 2 tests
test tests::test_addition ... ok
test tests::test_subtraction ... FAILED

failures:

---- tests::test_subtraction stdout ----
test result: ok. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out"""
        test_results = rust_compiler._parse_test_output(output)
        assert len(test_results) > 0
        
        # 查找测试结果
        passed_test = next((r for r in test_results if r["name"] == "tests::test_addition"), None)
        assert passed_test is not None
        assert passed_test["status"] == "passed"
        
        failed_test = next((r for r in test_results if r["name"] == "tests::test_subtraction"), None)
        assert failed_test is not None
        assert failed_test["status"] == "failed"
    
    def test_is_cargo_project(self, rust_compiler):
        """测试Cargo项目检测"""
        code_with_main = "fn main() { println!(\"Hello, world!\"); }"
        assert rust_compiler._is_cargo_project(code_with_main) is True
        
        code_with_use = "use std::collections::HashMap;"
        assert rust_compiler._is_cargo_project(code_with_use) is True
        
        code_simple = "println!(\"Hello\");\nlet x = 42;"
        assert rust_compiler._is_cargo_project(code_simple) is False
    
    def test_extract_rust_version_from_code(self, rust_compiler):
        """测试从代码提取Rust版本"""
        code_with_let_else = "fn main() { let Some(x) = some_option else { return }; }"
        version = rust_compiler._extract_rust_version_from_code(code_with_let_else)
        assert version >= "1.65"
        
        code_with_edition = "// Cargo.toml\nedition = \"2021\""
        version = rust_compiler._extract_rust_version_from_code(code_with_edition)
        assert version == "1.65"
    
    def test_suggest_fix_for_error(self, rust_compiler):
        """测试错误修复建议"""
        error_msg = "error[E0425]: cannot find value `x` in this scope"
        suggestion = rust_compiler._suggest_fix_for_error(error_msg)
        assert "未找到" in suggestion
        
        error_msg = "error[E0382]: borrow of moved value"
        suggestion = rust_compiler._suggest_fix_for_error(error_msg)
        assert "移动后" in suggestion
    
    @pytest.mark.asyncio
    async def test_check_syntax_success(self, rust_compiler):
        """测试成功语法检查"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        rust_compiler.container_manager._execute_command.return_value = (0, b"", b"")
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { println!(\"Hello, world!\"); }"
        options = RustExecutionOptions(rust_version="1.75")
        
        result = await rust_compiler.check_syntax(code, "main.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.SYNTAX
        assert result.errors == []
        assert result.warnings == []
        assert result.rust_version == "1.75"
    
    @pytest.mark.asyncio
    async def test_check_syntax_failure(self, rust_compiler):
        """测试失败语法检查"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        error_output = b"error[E0425]: cannot find value `x` in this scope in this scope\n --> main.rs:2:13\n  |\n2 |     println!(x);\n  |             ^ help: a local variable with a similar name exists: `y`"
        rust_compiler.container_manager._execute_command.return_value = (1, b"", error_output)
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { let y = 42; println!(x); }"
        options = RustExecutionOptions(rust_version="1.75")
        
        result = await rust_compiler.check_syntax(code, "main.rs", options)
        
        assert result.success is False
        assert result.check_type == RustCheckType.SYNTAX
        assert len(result.errors) > 0
        assert "cannot find value" in result.errors[0]
    
    @pytest.mark.asyncio
    async def test_compile_code_success(self, rust_compiler):
        """测试成功代码编译"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        rust_compiler.container_manager._execute_command.return_value = (0, b"", b"")
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { println!(\"Hello, world!\"); }"
        options = RustExecutionOptions(rust_version="1.75")
        
        result = await rust_compiler.compile_code(code, "main.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.COMPILE
        assert result.errors == []
        assert result.warnings == []
    
    @pytest.mark.asyncio
    async def test_execute_code_success(self, rust_compiler):
        """测试成功代码执行"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        
        # 第一次调用是编译命令
        # 第二次调用是执行命令
        rust_compiler.container_manager._execute_command.side_effect = [
            (0, b"", b""),  # 编译成功
            (0, b"Hello, world!\n", b"")  # 执行成功
        ]
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { println!(\"Hello, world!\"); }"
        options = RustExecutionOptions(rust_version="1.75")
        
        result = await rust_compiler.execute_code(code, "main.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.EXECUTE
        assert "Hello, world!" in result.output
    
    @pytest.mark.asyncio
    async def test_cargo_check_success(self, rust_compiler):
        """测试成功Cargo检查"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        cargo_output = b"    Checking project v0.1.0 (/app)\n     Finished dev [unoptimized + debuginfo] target(s) in 0.43s"
        mkdir_output = b""
        cp_output = b""
        
        # 先创建目录，复制文件，然后执行检查
        rust_compiler.container_manager._execute_command.side_effect = [
            (0, mkdir_output, b""),  # mkdir命令
            (0, cp_output, b""),      # cp命令
            (0, cargo_output, b"")    # cargo check命令
        ]
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { println!(\"Hello, world!\"); }"
        options = RustExecutionOptions(rust_version="1.75", project_name="test_project")
        
        result = await rust_compiler.cargo_check(code, "main.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.CARGO_CHECK
        assert result.errors == []
        assert "Finished dev" in result.output
    
    @pytest.mark.asyncio
    async def test_cargo_build_success(self, rust_compiler):
        """测试成功Cargo构建"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        cargo_output = b"   Compiling project v0.1.0 (/app)\n    Finished dev [unoptimized + debuginfo] target(s) in 1.23s"
        mkdir_output = b""
        cp_output = b""
        
        # 先创建目录，复制文件，然后执行构建
        rust_compiler.container_manager._execute_command.side_effect = [
            (0, mkdir_output, b""),  # mkdir命令
            (0, cp_output, b""),      # cp命令
            (0, cargo_output, b"")    # cargo build命令
        ]
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { println!(\"Hello, world!\"); }"
        options = RustExecutionOptions(rust_version="1.75", project_name="test_project")
        
        result = await rust_compiler.cargo_build(code, "main.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.CARGO_BUILD
        assert result.errors == []
        assert "Finished dev" in result.output
    
    @pytest.mark.asyncio
    async def test_cargo_run_success(self, rust_compiler):
        """测试成功Cargo运行"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        cargo_output = b"   Compiling project v0.1.0 (/app)\n     Finished dev [unoptimized + debuginfo] target(s) in 0.43s\n      Running `target/debug/project`\nHello, world!\n"
        mkdir_output = b""
        cp_output = b""
        
        # 先创建目录，复制文件，然后执行运行
        rust_compiler.container_manager._execute_command.side_effect = [
            (0, mkdir_output, b""),  # mkdir命令
            (0, cp_output, b""),      # cp命令
            (0, cargo_output, b"")    # cargo run命令
        ]
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { println!(\"Hello, world!\"); }"
        options = RustExecutionOptions(rust_version="1.75", project_name="test_project")
        
        result = await rust_compiler.cargo_run(code, "main.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.CARGO_RUN
        assert "Hello, world!" in result.output
    
    @pytest.mark.asyncio
    async def test_cargo_test_success(self, rust_compiler):
        """测试成功Cargo测试"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        cargo_output = b"""running 1 test
test tests::hello_test ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"""
        mkdir_output = b""
        cp_output = b""
        
        # 先创建目录，复制文件，然后执行测试
        rust_compiler.container_manager._execute_command.side_effect = [
            (0, mkdir_output, b""),  # mkdir命令
            (0, cp_output, b""),      # cp命令
            (0, cargo_output, b"")    # cargo test命令
        ]
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = """#[cfg(test)]
mod tests {
    #[test]
    fn hello_test() {
        assert_eq!(1, 1);
    }
}
"""
        options = RustExecutionOptions(rust_version="1.75", project_name="test_project")
        
        result = await rust_compiler.cargo_test(code, "lib.rs", options)
        
        assert result.success is True
        assert result.check_type == RustCheckType.CARGO_TEST
        assert "1 passed" in result.output
        assert len(result.test_results) > 0
    
    @pytest.mark.asyncio
    async def test_cargo_test_failure(self, rust_compiler):
        """测试Cargo测试失败"""
        # 设置模拟
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        cargo_output = b"""running 1 test
test tests::hello_test ... FAILED

failures:

---- tests::hello_test stdout ----
thread 'tests::hello_test' panicked at 'assertion failed: `(left == right)`
  left: `1`,
 right: `2`', src/lib.rs:5:5
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out"""
        mkdir_output = b""
        cp_output = b""
        
        # 先创建目录，复制文件，然后执行测试
        rust_compiler.container_manager._execute_command.side_effect = [
            (0, mkdir_output, b""),  # mkdir命令
            (0, cp_output, b""),      # cp命令
            (1, cargo_output, b"")    # cargo test命令失败
        ]
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = """#[cfg(test)]
mod tests {
    #[test]
    fn hello_test() {
        assert_eq!(1, 2);
    }
}
"""
        options = RustExecutionOptions(rust_version="1.75", project_name="test_project")
        
        result = await rust_compiler.cargo_test(code, "lib.rs", options)
        
        assert result.success is False
        assert result.check_type == RustCheckType.CARGO_TEST
        assert "0 passed; 1 failed" in result.output
        assert len(result.test_results) > 0
    
    @pytest.mark.asyncio
    async def test_check_syntax_timeout(self, rust_compiler):
        """测试语法检查超时"""
        # 设置模拟
        from app.docker.container_manager import ContainerTimeoutError
        mock_container = Mock()
        rust_compiler.container_manager.client.containers.get.return_value = mock_container
        rust_compiler.container_manager._execute_command.side_effect = ContainerTimeoutError("Timeout")
        
        # 使用上下文管理器模拟
        rust_compiler.file_manager.temporary_file_context.return_value.__aenter__.return_value = "test-file-id"
        
        code = "fn main() { while true {} }"
        options = RustExecutionOptions(rust_version="1.75", timeout=1)
        
        result = await rust_compiler.check_syntax(code, "main.rs", options)
        
        assert result.success is False
        assert result.check_type == RustCheckType.SYNTAX
        assert "超时" in result.errors[0]
    
    def test_rust_check_result_to_dict(self):
        """测试RustCheckResult转换为字典"""
        result = RustCheckResult(
            success=True,
            check_type=RustCheckType.SYNTAX,
            rust_version="1.75",
            container_id="test-container-id",
            dependencies=["serde"],
            test_results=[{"name": "test_example", "status": "passed"}],
            cargo_info={"dependencies": ["serde"], "compiling": "my_project v0.1.0"}
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["success"] is True
        assert result_dict["check_type"] == "syntax"
        assert result_dict["rust_version"] == "1.75"
        assert result_dict["container_id"] == "test-container-id"
        assert len(result_dict["dependencies"]) == 1
        assert len(result_dict["test_results"]) == 1
        assert result_dict["test_results"][0]["name"] == "test_example"
        assert "dependencies" in result_dict["cargo_info"]