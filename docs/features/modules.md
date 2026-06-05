# Modules (public build)

**19 plugins** from `config/modules_catalog.json`. Tier **D/DEV** modules are not shipped.

| Module | Tier | Role |
|--------|------|------|
| chat-orchestrator | A | Main dialogue → brain |
| external_apis | A | Weather, external HTTP |
| skills | A | Builtin skills pack |
| memory | A | Mem0 + slash memory |
| dialog_memory_recall | A | Recall prior dialogue |
| group_behavior | A | Group chat behavior |
| persona_engine | A | Persona / style |
| schedule_module | A | Schedule slash |
| security_layer | A | Policy / encryption tools |
| user_system | A | User profile |
| light_reminders | A | Reminders |
| books_rag | B | Books RAG |
| error_memory | B | Error journal tool |
| image_generator | B | Image generation |
| imaging | B | Media processing |
| rag | B | RAG slash |
| tools | B | Slash utilities pack |
| vision_describe | B | Image description |
| vision_ocr | B | OCR |

Regenerate catalog docs (private): `python scripts/module_class_audit.py --write-docs`

Plugin folders: `modules/<folder>/module.json`
