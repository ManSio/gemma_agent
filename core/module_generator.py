"""
Module Generator — DEV-only scaffold (не в hot path orchestrator).

См. docs/DEAD_CODE_DEFERRED_RU.md
"""
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.json_atomic import atomic_write_json
from core.plugin_requirements import write_plugin_pip_sidecar

class ModuleGenerator:
    """Generate new modules dynamically"""
    
    def __init__(self, modules_path: str = "./modules"):
        self.modules_path = Path(modules_path)
        
    def create_module_from_template(
        self,
        module_name: str,
        description: str,
        commands: List[Dict[str, Any]] = None,
        dependencies: List[str] = None,
        pip_requirements: List[str] = None,
    ) -> bool:
        """Create a new module from a template"""
        try:
            # Create module directory
            module_dir = self.modules_path / module_name
            module_dir.mkdir(exist_ok=True)
            
            # Create module.json
            manifest = self._generate_manifest(
                module_name,
                description,
                commands,
                dependencies,
                pip_requirements,
            )
            manifest_file = module_dir / "module.json"
            if not atomic_write_json(manifest_file, manifest):
                return False

            write_plugin_pip_sidecar(module_dir, pip_requirements)
            
            # Create module.py
            module_file = module_dir / "module.py"
            with open(module_file, 'w', encoding='utf-8') as f:
                f.write(self._generate_module_content(module_name, description))
            
            # Create tests.py
            tests_file = module_dir / "tests.py"
            with open(tests_file, 'w', encoding='utf-8') as f:
                f.write(self._generate_tests_content(module_name))
                
            return True
            
        except Exception as e:
            print(f"Error creating module: {e}")
            return False
    
    def _generate_manifest(
        self,
        name: str,
        description: str,
        commands: List[Dict[str, Any]] = None,
        dependencies: List[str] = None,
        pip_requirements: List[str] = None,
    ) -> Dict[str, Any]:
        """Generate module manifest"""
        return {
            "name": name,
            "version": "1.0.0",
            "type": "tool",
            "description": description,
            "entrypoint": f"modules.{name}.module:{name.capitalize()}Module",
            "input_types": ["text"],
            "output_types": ["text"],
            "capabilities": [],
            "prompts": {},
            "commands": commands or [],
            "buttons": [],
            "config_schema": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "requires": dependencies or [],
            "pip_requirements": list(pip_requirements or []),
        }
    
    def _generate_module_content(self, name: str, description: str) -> str:
        """Generate module Python content"""
        class_name = name.capitalize() + "Module"
        return f'''"""
{name.capitalize()} Module - {description}

Дополнительные pip-пакеты перечисляйте в module.json -> pip_requirements
(и при сборке Docker они подтянутся через scripts/merge_plugin_requirements.py).
"""
from typing import Any, Dict, List
from core.models import Input, Output

class {class_name}:
    """{name.capitalize()} module implementation"""
    
    def __init__(self):
        """Initialize module"""
        pass
    
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        """Main execution method"""
        input_data = args.get("input", {{}})
        payload = input_data.get("payload", "")
        
        return [Output(
            type="text",
            payload=f"Module {{name}} executed - payload: {{payload}}",
            meta={{"module": "{name}"}}
        )]
'''
    
    def _generate_tests_content(self, name: str) -> str:
        """Generate test content"""
        class_name = name.capitalize() + "Module"
        return f'''"""
Tests for {name.capitalize()} module
"""
import unittest
from modules.{name}.module import {class_name}

class Test{name.capitalize()}Module(unittest.TestCase):
    
    def setUp(self):
        self.module = {class_name}()
    
    def test_execute(self):
        """Test module execution"""
        result = self.module.execute({{
            "input": {{
                "payload": "test input"
            }}
        }})
        
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

if __name__ == "__main__":
    unittest.main()
'''