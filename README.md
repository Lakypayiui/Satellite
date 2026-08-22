# Satellite — Framework general de microagentes para desarrollo de software

Framework instalable que transforma cualquier repositorio en un entorno dirigido por microagentes; CLI `satellite`; config de proyecto consumidor en `.satellite/`.

## Estado

**FASE 1 (Core de Agentes) en curso**

| Fase | Descripción |
|------|-------------|
| 1 | Core de Agentes |
| 2 | Runtime determinista |
| 3 | Sistema de herramientas |
| 4 | Memoria y contexto |
| 5 | Planificación y ejecución |
| 6 | Comunicación entre agentes |
| 7 | Observabilidad y logging |
| 8 | Persistencia de estado |
| 9 | Sandbox y seguridad |
| 10 | CLI `satellite` |
| 11 | Configuración `.satellite/` |
| 12 | Plugin system |
| 13 | Marketplace de agentes |
| 14 | Integración Git |
| 15 | CI/CD nativo |
| 16 | Testing framework |
| 17 | Documentación generada |
| 18 | Multi-repo orchestration |
| 19 | Distributed agents |
| 20 | Self-improvement loops |
| 21 | Policy engine |
| 22 | Cost tracking |
| 23 | Telemetry & analytics |
| 24 | GA release |

## Build

```bash
cmake -G Ninja -S . -B build && cmake --build build
```

Requiere g++ MSYS2 UCRT64 en PATH.