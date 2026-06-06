# Self-Programming System

This document describes the self-programming capabilities implemented for the Universal Social Assistant.

## Overview

The self-programming system enables the assistant to automatically generate, modify, and repair modules and libraries without human intervention. This capability is crucial for a self-healing platform, allowing the system to adapt to changing requirements and fix issues autonomously.

## Architecture Components

### SelfProgrammingModule

The main engine is implemented in `core/self_programming.py` and handles:

- Module generation from descriptions
- Module repair and recovery 
- Library repair mechanisms
- System analysis and issue detection
- Patch generation and application

### SelfHealingEngine

The self-healing engine in `core/self_healing.py` provides:

- Continuous monitoring of system health
- Automatic repair of failed modules and libraries
- Fallback management for broken components
- Scheduled health checks

## Module Generation

### `/generate_module` Command

The system supports creating new modules automatically through a command syntax:

```bash
/generate_module <module_name> "<description>" [commands] [dependencies]
```

For example:
```bash
/generate_module "vocabulary_trainer" "Create an English vocabulary training module" 
```

### Generation Process

When the `/generate_module` command is executed:

1. **Directory Creation**: Creates `/modules/<module_name>/` directory
2. **Manifest Generation**: Creates a complete `module.json` based on specification
3. **Code Generation**: Generates skeleton `module.py` with proper structure
4. **Test Generation**: Creates `tests.py` with basic test framework
5. **Registration**: Automatically registers the new module in the system
6. **Validation**: Runs tests to ensure proper functionality

### Example Generated Module

The vocabulary trainer module generated using the self-programming system demonstrates:

- Proper command registration (`/vocab`, `/quiz`)
- Dependency handling (`requires`)
- Structured implementation using core models
- Testable code structure

## Self-Repair Mechanisms

The system implements three major repair modes:

### Module Repair

When a module fails:
1. System identifies the failure
2. Attempts to reload and re-test the module
3. If that fails, uses AI-generated patches to fix issues
4. Re-runs tests to verify repair success

### Library Repair

When a library fails:
1. Identifies broken/missing library
2. Attempts to enable fallback library if available
3. If fallback also fails, logs failure and disables dependent modules
4. Can use AI to create replacement library implementations

### System-Level Repair

The self-healing engine:
- Continuously monitors all modules and libraries
- Detects health issues proactively
- Automatically initiates repair processes
- Maintains system stability during repair operations

## API Integration

The self-programming system exposes the following API endpoints:

### `/api/v1/generate-module` 

Creates a new module based on specification:

```json
{
  "module_name": "new_module",
  "description": "Description of what the module should do",
  "commands": [
    {
      "name": "command_name",
      "trigger": "/command",
      "description": "Command description"
    }
  ],
  "dependencies": ["dependency1", "dependency2"]
}
```

### `/api/v1/self-repair`

Repairs failed modules or libraries:

```json
{
  "module_name": "broken_module", 
  "library_name": "broken_library"
}
```

## Repair Process

The repair workflow follows these steps:

1. **Issue Detection**: Identify what is broken (module or library)
2. **Diagnosis**: Analyze root cause of failure
3. **Patch Generation**: Create appropriate fix (using AI in real system)
4. **Application**: Apply the patch to resolve the issue
5. **Verification**: Run tests to confirm fix worked
6. **Status Update**: Mark repaired components as healthy

## Self-Programming Patterns

### Autonomous Mode

In autonomous mode, the system:
- Automatically generates modules when requirements aren't met
- Identifies and fixes recurring issues
- Learns from patterns to reduce future failures

### Semi-Automatic Mode

In semi-automatic mode:
- System detects missing functionality  
- Proposes new modules to the user for approval
- Allows human review before implementation

### Manual Mode  

In manual mode:
- Users explicitly request module generation
- Provides full control over module specifications
- No automatic action without explicit approval

## Integration with Core Systems

The self-programming system integrates deeply with:

### Module Loader
- Automatically detects and loads newly generated modules
- Runs tests for module verification
- Handles module activation/deactivation

### Library Loader
- Ensures new modules' dependencies are available
- Manages fallback libraries when new components fail
- Integrates with registry for dependency tracking

### Plugin Registry
- Registers new modules with the system
- Manages module lifecycle
- Ensures consistency of module interfaces

### Fallback System
- Uses fallback libraries for broken components during repair
- Coordinates with the library healing mechanism
- Maintains system uptime during repairs

## Security Considerations

### Code Generation Safety
- Generated code follows strict patterns
- Uses core libraries only (no external dependencies)
- All generated code is subjected to unit testing
- No direct system access or modification

### Repair Isolation
- Repair processes run in isolated contexts
- Changes are verified before application
- System rollback capabilities available
- Audit logs for all self-programming actions

## API Endpoints

The system exposes the following endpoints for self-programming:

- `POST /api/v1/generate-module` - Generate new modules
- `POST /api/v1/self-repair` - Repair broken components 
- `GET /api/v1/health` - System health with self-programming status
- `GET /api/v1/monitor` - Monitor self-programming system status

## Future Extensions

The self-programming system is designed to allow for:

- Enhanced AI assistance in code generation
- More sophisticated patch algorithms 
- Automated performance optimization
- Proactive system optimization based on usage patterns
- Integration with external knowledge bases