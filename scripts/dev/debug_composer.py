import re

def _parse_composer_output(output: str):
    """解析Composer输出，返回(已安装包, 错误信息)"""
    installed_packages = []
    errors = []
    
    lines = output.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        print(f"Line {i}: '{line}'")
        
        # 检测成功安装的包
        if "Generating autoload files" in line:
            # 找到依赖安装成功的标志
            continue
        
        # 检测包安装信息
        if " - Installing " in line or " - Updating " in line:
            print(f"Found package line: '{line}'")
            # 使用简单的字符串分割提取包名
            parts = line.split()
            print(f"Parts: {parts}")
            if len(parts) >= 3 and (parts[1] == "Installing" or parts[1] == "Updating"):
                package_name = parts[2]
                print(f"Package name extracted: {package_name}")
                installed_packages.append(package_name)
        
        # 检测错误
        if line.startswith("[ErrorException]") or line.startswith("[RuntimeException]"):
            errors.append(line)
    
    return installed_packages, errors

# 测试用例
output = """
 - Installing monolog/monolog (2.3.5)
 - Updating symfony/console (5.4.7)
Generating autoload files
"""

installed_packages, errors = _parse_composer_output(output)
print(f"\nInstalled packages: {installed_packages}")
print(f"Errors: {errors}")