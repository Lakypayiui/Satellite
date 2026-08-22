# sample-project

Repositorio consumidor de ejemplo del framework **Satellite**.

Este directorio NO es parte del framework: demuestra cómo cualquier repositorio
se convierte en un proyecto consumidor con `satellite init`.

## Uso (con el framework instalado o desde el build)

```bash
satellite init            # crea .satellite/ (config, registry, agents, context, executions)
satellite agents          # lista los agentes nativos del framework
satellite context build   # analiza el proyecto (C++ detectado)
satellite context inspect # detalle del contexto
satellite doctor          # diagnóstico del proyecto
satellite run "objetivo"  # orquesta agentes vía LLM (requiere DEEPSEEK_API_KEY)
```

## Notas

- `.satellite/` es estado del proyecto consumidor (no se commitea: ver .gitignore).
- El framework permanece instalado externamente: este repositorio solo contiene su propio código.
