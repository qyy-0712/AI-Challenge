"""
文件系统安全交互管理器

提供宿主机与Docker容器之间的安全文件传输、权限控制和临时文件管理功能。
"""

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Any, AsyncGenerator, Tuple, Union
import uuid

import docker
from docker.errors import DockerException, APIError
from docker.models.containers import Container


# 配置日志
logger = logging.getLogger(__name__)


class FileOperationType(Enum):
    """文件操作类型"""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    COPY = "copy"
    MOVE = "move"


class FilePermission(Enum):
    """文件权限枚举"""
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    EXECUTE = "execute"
    NONE = "none"


@dataclass
class FileMetadata:
    """文件元数据"""
    path: str
    size: int
    checksum: str
    created_at: float
    modified_at: float
    permission: FilePermission
    is_temporary: bool = False
    container_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "path": self.path,
            "size": self.size,
            "checksum": self.checksum,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "permission": self.permission.value,
            "is_temporary": self.is_temporary,
            "container_id": self.container_id
        }


@dataclass
class FileTransferConfig:
    """文件传输配置"""
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    allowed_extensions: Set[str] = field(default_factory=lambda: {
        ".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".php", ".rb",
        ".txt", ".md", ".json", ".xml", ".yaml", ".yml"
    })
    blocked_paths: Set[str] = field(default_factory=lambda: {
        "/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys"
    })
    temp_dir: Optional[str] = None
    cleanup_interval: int = 300  # 5分钟
    max_temp_files: int = 100


class FileManagerError(Exception):
    """文件管理器基础异常"""
    pass


class FileSecurityError(FileManagerError):
    """文件安全异常"""
    pass


class FileTransferError(FileManagerError):
    """文件传输异常"""
    pass


class PathValidationError(FileManagerError):
    """路径验证异常"""
    pass


class SecurePathValidator:
    """安全路径验证器"""
    
    def __init__(self, blocked_paths: Set[str] = None):
        self.blocked_paths = blocked_paths or {
            "/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys"
        }
    
    def validate_path(self, path: Union[str, Path]) -> Path:
        """验证路径安全性"""
        try:
            path_obj = Path(path).resolve()
            
            # 检查路径是否存在危险目录
            for blocked in self.blocked_paths:
                if str(path_obj).startswith(blocked):
                    raise PathValidationError(f"路径包含禁止访问的目录: {path}")
            
            # 检查路径遍历攻击
            if ".." in str(path_obj):
                raise PathValidationError(f"检测到路径遍历攻击: {path}")
            
            return path_obj
            
        except Exception as e:
            if isinstance(e, PathValidationError):
                raise
            raise PathValidationError(f"路径验证失败: {path}, 错误: {e}")
    
    def is_safe_filename(self, filename: str) -> bool:
        """检查文件名是否安全"""
        # 检查危险字符
        dangerous_chars = ["<", ">", ":", "\"", "|", "?", "*"]
        if any(char in filename for char in dangerous_chars):
            return False
        
        # 检查保留名称（Windows）
        reserved_names = [
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
        ]
        
        name_without_ext = Path(filename).stem.upper()
        if name_without_ext in reserved_names:
            return False
        
        return True


class TemporaryFileManager:
    """临时文件管理器"""
    
    def __init__(self, config: FileTransferConfig):
        self.config = config
        self.temp_dir = Path(config.temp_dir or tempfile.mkdtemp(prefix="costrict_temp_"))
        self.temp_files: Dict[str, FileMetadata] = {}
        self._lock = threading.RLock()
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # 确保临时目录存在
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"临时文件管理器初始化，目录: {self.temp_dir}")
    
    def create_temp_file(self, 
                        content: bytes, 
                        filename: str, 
                        container_id: Optional[str] = None) -> str:
        """创建临时文件"""
        with self._lock:
            # 检查临时文件数量限制
            if len(self.temp_files) >= self.config.max_temp_files:
                self._cleanup_old_files()
                
                if len(self.temp_files) >= self.config.max_temp_files:
                    raise FileTransferError("临时文件数量超过限制")
            
            # 生成唯一文件名
            file_id = str(uuid.uuid4())
            safe_filename = f"{file_id}_{filename}"
            file_path = self.temp_dir / safe_filename
            
            try:
                # 写入文件
                file_path.write_bytes(content)
                
                # 计算校验和
                checksum = hashlib.sha256(content).hexdigest()
                
                # 创建元数据
                metadata = FileMetadata(
                    path=str(file_path),
                    size=len(content),
                    checksum=checksum,
                    created_at=time.time(),
                    modified_at=time.time(),
                    permission=FilePermission.READ_WRITE,
                    is_temporary=True,
                    container_id=container_id
                )
                
                self.temp_files[file_id] = metadata
                logger.info(f"创建临时文件: {file_id}, 路径: {file_path}")
                
                return file_id
                
            except Exception as e:
                # 清理失败的文件
                if file_path.exists():
                    file_path.unlink()
                raise FileTransferError(f"创建临时文件失败: {e}")
    
    def get_temp_file(self, file_id: str) -> Optional[FileMetadata]:
        """获取临时文件元数据"""
        with self._lock:
            return self.temp_files.get(file_id)
    
    def get_temp_file_path(self, file_id: str) -> Optional[Path]:
        """获取临时文件路径"""
        with self._lock:
            metadata = self.temp_files.get(file_id)
            if metadata:
                return Path(metadata.path)
            return None
    
    def delete_temp_file(self, file_id: str) -> bool:
        """删除临时文件"""
        with self._lock:
            metadata = self.temp_files.pop(file_id, None)
            if metadata:
                try:
                    file_path = Path(metadata.path)
                    if file_path.exists():
                        file_path.unlink()
                    logger.info(f"删除临时文件: {file_id}")
                    return True
                except Exception as e:
                    logger.error(f"删除临时文件失败: {file_id}, 错误: {e}")
            return False
    
    def _cleanup_old_files(self) -> int:
        """清理旧文件"""
        current_time = time.time()
        files_to_remove = []
        
        for file_id, metadata in self.temp_files.items():
            if current_time - metadata.created_at > self.config.cleanup_interval:
                files_to_remove.append(file_id)
        
        for file_id in files_to_remove:
            self.delete_temp_file(file_id)
        
        return len(files_to_remove)
    
    async def start_cleanup_task(self) -> None:
        """启动清理任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            logger.info("临时文件清理任务已启动")
    
    async def _periodic_cleanup(self) -> None:
        """定期清理临时文件"""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                cleaned_count = self._cleanup_old_files()
                if cleaned_count > 0:
                    logger.info(f"清理了 {cleaned_count} 个临时文件")
            except asyncio.CancelledError:
                logger.info("临时文件清理任务已取消")
                break
            except Exception as e:
                logger.error(f"临时文件清理出错: {e}")
    
    def stop_cleanup_task(self) -> None:
        """停止清理任务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("临时文件清理任务已停止")
    
    def cleanup_all(self) -> int:
        """清理所有临时文件"""
        with self._lock:
            file_ids = list(self.temp_files.keys())
            for file_id in file_ids:
                self.delete_temp_file(file_id)
            return len(file_ids)


class DockerFileTransfer:
    """Docker文件传输管理器"""
    
    def __init__(self, docker_client: docker.DockerClient):
        self.client = docker_client
        self.path_validator = SecurePathValidator()
    
    def copy_to_container(self, 
                         container: Container, 
                         source_path: Union[str, Path], 
                         dest_path: str) -> bool:
        """复制文件到容器"""
        try:
            source = Path(source_path)
            if not source.exists():
                raise FileTransferError(f"源文件不存在: {source}")
            
            # 验证目标路径
            dest_path_obj = self.path_validator.validate_path(dest_path)
            
            # 复制文件到容器
            with source.open('rb') as f:
                container.put_archive(os.path.dirname(dest_path), f.read())
            
            logger.info(f"文件已复制到容器 {container.id}: {source} -> {dest_path}")
            return True
            
        except Exception as e:
            logger.error(f"复制文件到容器失败: {e}")
            raise FileTransferError(f"复制文件到容器失败: {e}")
    
    def copy_from_container(self, 
                           container: Container, 
                           source_path: str, 
                           dest_path: Union[str, Path]) -> bool:
        """从容器复制文件"""
        try:
            # 验证源路径
            source_path_obj = self.path_validator.validate_path(source_path)
            dest = Path(dest_path)
            
            # 确保目标目录存在
            dest.parent.mkdir(parents=True, exist_ok=True)
            
            # 从容器获取文件
            bits, _ = container.get_archive(source_path)
            
            with dest.open('wb') as f:
                for chunk in bits:
                    f.write(chunk)
            
            logger.info(f"文件已从容器 {container.id} 复制: {source_path} -> {dest}")
            return True
            
        except Exception as e:
            logger.error(f"从容器复制文件失败: {e}")
            raise FileTransferError(f"从容器复制文件失败: {e}")
    
    def create_volume_mount(self, 
                           host_path: Union[str, Path], 
                           container_path: str, 
                           mode: str = "ro") -> Dict[str, Dict[str, str]]:
        """创建卷挂载配置"""
        try:
            host = Path(host_path).resolve()
            
            # 验证路径
            self.path_validator.validate_path(host)
            self.path_validator.validate_path(container_path)
            
            return {
                str(host): {
                    "bind": container_path,
                    "mode": mode
                }
            }
            
        except Exception as e:
            logger.error(f"创建卷挂载配置失败: {e}")
            raise FileTransferError(f"创建卷挂载配置失败: {e}")


class FileManager:
    """文件系统安全交互管理器"""
    
    def __init__(self, 
                 config: Optional[FileTransferConfig] = None,
                 docker_client: Optional[docker.DockerClient] = None):
        self.config = config or FileTransferConfig()
        self.temp_manager = TemporaryFileManager(self.config)
        self.docker_client = docker_client or docker.from_env()
        self.docker_transfer = DockerFileTransfer(self.docker_client)
        self.path_validator = SecurePathValidator(self.config.blocked_paths)
        
        logger.info("文件管理器初始化完成")
    
    def validate_file_content(self, content: bytes, filename: str) -> bool:
        """验证文件内容"""
        # 检查文件大小
        if len(content) > self.config.max_file_size:
            raise FileSecurityError(f"文件大小超过限制: {len(content)} > {self.config.max_file_size}")
        
        # 检查文件扩展名
        file_ext = Path(filename).suffix.lower()
        if file_ext and file_ext not in self.config.allowed_extensions:
            raise FileSecurityError(f"不允许的文件扩展名: {file_ext}")
        
        # 检查文件名安全性
        if not self.path_validator.is_safe_filename(filename):
            raise FileSecurityError(f"不安全的文件名: {filename}")
        
        return True
    
    def create_secure_temp_file(self, 
                                content: bytes, 
                                filename: str, 
                                container_id: Optional[str] = None) -> str:
        """创建安全临时文件"""
        # 验证文件内容
        self.validate_file_content(content, filename)
        
        # 创建临时文件
        return self.temp_manager.create_temp_file(content, filename, container_id)
    
    def get_temp_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """获取临时文件信息"""
        metadata = self.temp_manager.get_temp_file(file_id)
        return metadata.to_dict() if metadata else None
    
    def setup_file_mounts(self, 
                         file_ids: List[str], 
                         container_base_path: str = "/workspace") -> Dict[str, Dict[str, str]]:
        """设置文件挂载"""
        mounts = {}
        
        for file_id in file_ids:
            file_path = self.temp_manager.get_temp_file_path(file_id)
            if file_path:
                container_path = f"{container_base_path}/{file_id}"
                mount = self.docker_transfer.create_volume_mount(
                    file_path, 
                    container_path, 
                    mode="ro"  # 只读挂载
                )
                mounts.update(mount)
        
        return mounts
    
    async def cleanup_temp_files(self, file_ids: List[str] = None) -> int:
        """清理临时文件"""
        if file_ids:
            count = 0
            for file_id in file_ids:
                if self.temp_manager.delete_temp_file(file_id):
                    count += 1
            return count
        else:
            return self.temp_manager.cleanup_all()
    
    async def start_cleanup_task(self) -> None:
        """启动清理任务"""
        await self.temp_manager.start_cleanup_task()
    
    def stop_cleanup_task(self) -> None:
        """停止清理任务"""
        self.temp_manager.stop_cleanup_task()
    
    @asynccontextmanager
    async def temporary_file_context(self, 
                                    content: bytes, 
                                    filename: str, 
                                    container_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """临时文件上下文管理器"""
        file_id = None
        try:
            file_id = self.create_secure_temp_file(content, filename, container_id)
            yield file_id
        finally:
            if file_id:
                await self.cleanup_temp_files([file_id])
    
    def get_file_checksum(self, file_path: Union[str, Path]) -> str:
        """计算文件校验和"""
        path = Path(file_path)
        if not path.exists():
            raise FileTransferError(f"文件不存在: {path}")
        
        with path.open('rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    
    def verify_file_integrity(self, file_id: str, expected_checksum: str) -> bool:
        """验证文件完整性"""
        file_path = self.temp_manager.get_temp_file_path(file_id)
        if not file_path:
            return False
        
        actual_checksum = self.get_file_checksum(file_path)
        return actual_checksum == expected_checksum