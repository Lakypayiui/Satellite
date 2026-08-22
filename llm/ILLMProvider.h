#pragma once

#include "LLMTypes.h"

namespace satellite::llm
{

/**
 * Interfaz base para proveedores de LLM.
 * El framework depende SOLO de esta interfaz; el runtime (dispatcher/agentes)
 * nunca llama al LLM directamente.
 */
class ILLMProvider
{
public:
    virtual ~ILLMProvider() = default;

    /**
     * @return Nombre identificador del proveedor (ej. "deepseek").
     */
    virtual std::string name() const = 0;

    /**
     * Realiza una petición de completado al proveedor.
     * @param request Petición con system_prompt, user_prompt, max_tokens y temperature.
     * @return Respuesta con el texto generado, tokens consumidos y estado de éxito.
     */
    virtual LLMResponse complete(const LLMRequest& request) = 0;
};

} // namespace satellite::llm