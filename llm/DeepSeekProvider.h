#pragma once

#include "llm/ILLMProvider.h"
#include <json.hpp>
#include <string>

namespace satellite::llm
{

/**
 * Construye el payload JSON para la API de DeepSeek.
 * Función libre y testeable (sin red).
 * @param request Petición con los prompts y parámetros.
 * @param model Nombre del modelo a usar (ej. "deepseek-chat").
 * @return Objeto JSON listo para enviar.
 */
nlohmann::json build_deepseek_payload(const LLMRequest& request, const std::string& model);

/**
 * Parsea la respuesta JSON de DeepSeek a LLMResponse.
 * Función libre y testeable (sin red).
 * @param response_json Respuesta cruda del servidor.
 * @return LLMResponse con ok, text, finish_reason, tokens y error_message si falla.
 */
LLMResponse parse_deepseek_response(const nlohmann::json& response_json);

/**
 * Adaptador para el proveedor DeepSeek.
 * Usa curl.exe como subproceso (presente en Windows 10+, MSYS2, Linux/macOS).
 * CERO dependencias de terceros. El body va a archivo temporal y se pasa
 * con --data @archivo (el prompt nunca viaja en argv). La API key va en el
 * header Authorization (gestión segura de secretos es FASE 17).
 */
class DeepSeekProvider : public ILLMProvider
{
public:
    /**
     * @param api_key Clave de API de DeepSeek.
     * @param base_url URL base de la API (por defecto "https://api.deepseek.com").
     * @param model Nombre del modelo (por defecto "deepseek-chat").
     */
    DeepSeekProvider(std::string api_key, std::string base_url = "https://api.deepseek.com", std::string model = "deepseek-chat");

    std::string name() const override;

    LLMResponse complete(const LLMRequest& request) override;

private:
    /**
     * Realiza una petición HTTP POST con body JSON usando curl.exe.
     * Escribe el body a un archivo temporal único, ejecuta curl redirigiendo
     * stdout a un archivo de salida temporal, lee la respuesta y borra ambos
     * temporales. En Windows el comando se ejecuta vía cmd; rutas con \ escapadas
     * correctamente.
     * @param url URL completa del endpoint.
     * @param auth_header Header Authorization completo (ej. "Authorization: Bearer xxx").
     * @param body Body JSON como string.
     * @return Respuesta cruda del servidor, o string vacío si falla curl.
     */
    std::string http_post_json(const std::string& url, const std::string& auth_header, const std::string& body);

    std::string api_key_;
    std::string base_url_;
    std::string model_;
};

} // namespace satellite::llm