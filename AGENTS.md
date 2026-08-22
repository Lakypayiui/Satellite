# Reglas de trabajo para agentes de IA (OpenCode) en este repositorio

- Este repo es SOLO el framework Satellite; jamás crear código de aplicación consumidora aquí.
- Estándar: C++17, estilo con llaves Allman, comentarios en español.
- Toda afirmación de que algo funciona debe estar respaldada por ejecución real (build o tests); está prohibido inventar resultados.
- Los agentes trabajan por fases; no agregar funcionalidad fuera de la fase solicitada.
- El runtime es determinista: el LLM decide, el runtime ejecuta; nunca generar código que permita ejecución arbitraria desde texto del LLM.