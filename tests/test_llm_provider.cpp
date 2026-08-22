// Mini framework de test para llm provider (FASE 6)
// Sin dependencias externas: solo C++17 estándar y nlohmann/json

#include <iostream>
#include <string>
#include <memory>

#include <json.hpp>
#include "llm/LLMTypes.h"
#include "llm/ILLMProvider.h"
#include "llm/DeepSeekProvider.h"

using namespace satellite::llm;

int g_passed = 0;
int g_failed = 0;

#define CHECK(desc, cond) \
    do { \
        if (cond) { \
            std::cout << "PASSED: " << desc << "\n"; \
            ++g_passed; \
        } else { \
            std::cout << "FAILED: " << desc << ": " #cond "\n"; \
            ++g_failed; \
        } \
    } while (false)

// FakeProvider local que implementa ILLMProvider
class FakeProvider : public ILLMProvider
{
public:
    std::string name() const override
    {
        return "fake";
    }

    LLMResponse complete(const LLMRequest&) override
    {
        LLMResponse response;
        response.ok = true;
        response.text = "respuesta fake";
        response.finish_reason = "stop";
        response.prompt_tokens = 10;
        response.completion_tokens = 5;
        response.total_tokens = 15;
        response.error_message = "";
        return response;
    }
};

void test_fake_provider_interface()
{
    FakeProvider fake;
    ILLMProvider* provider = &fake;

    // Verificar contrato de la interfaz vía puntero base
    CHECK("FakeProvider name() == \"fake\"", provider->name() == "fake");

    LLMRequest request;
    request.system_prompt = "sistema";
    request.user_prompt = "usuario";
    request.max_tokens = 0;
    request.temperature = 0.0;

    LLMResponse response = provider->complete(request);

    CHECK("FakeProvider complete ok == true", response.ok == true);
    CHECK("FakeProvider complete text", response.text == "respuesta fake");
    CHECK("FakeProvider complete finish_reason", response.finish_reason == "stop");
    CHECK("FakeProvider complete prompt_tokens", response.prompt_tokens == 10);
    CHECK("FakeProvider complete completion_tokens", response.completion_tokens == 5);
    CHECK("FakeProvider complete total_tokens", response.total_tokens == 15);
    CHECK("FakeProvider complete error_message empty", response.error_message.empty());
    CHECK("FakeProvider total_tokens == prompt + completion", response.total_tokens == response.prompt_tokens + response.completion_tokens);
}

void test_build_deepseek_payload_basic()
{
    LLMRequest request;
    request.system_prompt = "sistema";
    request.user_prompt = "usuario";
    request.max_tokens = 0;
    request.temperature = 0.0;

    nlohmann::json j = build_deepseek_payload(request, "deepseek-chat");

    CHECK("payload model == deepseek-chat", j["model"] == "deepseek-chat");
    CHECK("payload messages size == 2", j["messages"].size() == 2);
    CHECK("payload messages[0].role == system", j["messages"][0]["role"] == "system");
    CHECK("payload messages[0].content == sistema", j["messages"][0]["content"] == "sistema");
    CHECK("payload messages[1].role == user", j["messages"][1]["role"] == "user");
    CHECK("payload messages[1].content == usuario", j["messages"][1]["content"] == "usuario");
    CHECK("payload NO contiene max_tokens (0 omitido)", !j.contains("max_tokens"));
    CHECK("payload NO contiene temperature (0.0 omitido)", !j.contains("temperature"));
    CHECK("payload stream == false", j["stream"] == false);
}

void test_build_deepseek_payload_with_params()
{
    LLMRequest request;
    request.system_prompt = "sistema";
    request.user_prompt = "usuario";
    request.max_tokens = 512;
    request.temperature = 0.7;

    nlohmann::json j = build_deepseek_payload(request, "deepseek-chat");

    CHECK("payload max_tokens == 512", j["max_tokens"] == 512);
    CHECK("payload temperature == 0.7", j["temperature"] == 0.7);
}

void test_parse_deepseek_response_valid()
{
    nlohmann::json response_json = {
        {"choices", {{
            {"message", {{"content", "hola mundo"}}},
            {"finish_reason", "stop"}
        }}},
        {"usage", {
            {"prompt_tokens", 10},
            {"completion_tokens", 5},
            {"total_tokens", 15}
        }}
    };

    LLMResponse response = parse_deepseek_response(response_json);

    CHECK("parse valid ok == true", response.ok == true);
    CHECK("parse valid text == hola mundo", response.text == "hola mundo");
    CHECK("parse valid finish_reason == stop", response.finish_reason == "stop");
    CHECK("parse valid prompt_tokens == 10", response.prompt_tokens == 10);
    CHECK("parse valid completion_tokens == 5", response.completion_tokens == 5);
    CHECK("parse valid total_tokens == 15", response.total_tokens == 15);
    CHECK("parse valid error_message empty", response.error_message.empty());
}

void test_parse_deepseek_response_error()
{
    nlohmann::json response_json = {
        {"error", {{"message", "invalid api key"}}}
    };

    LLMResponse response = parse_deepseek_response(response_json);

    CHECK("parse error ok == false", response.ok == false);
    CHECK("parse error error_message contains invalid api key", response.error_message.find("invalid api key") != std::string::npos);
}

void test_parse_deepseek_response_no_choices()
{
    nlohmann::json response_json = {};

    LLMResponse response = parse_deepseek_response(response_json);

    CHECK("parse no choices ok == false", response.ok == false);
    CHECK("parse no choices error_message not empty", !response.error_message.empty());
}

void test_deepseek_provider_name()
{
    DeepSeekProvider provider("test_key", "https://api.deepseek.com", "deepseek-chat");
    CHECK("DeepSeekProvider name() == deepseek", provider.name() == "deepseek");
}

void test_deepseek_provider_network_error()
{
    // Usar URL inválida (puerto 1 en localhost) para forzar error de conexión inmediato
    // curl --max-time 60 fallará al instante al no poder conectar a 127.0.0.1:1
    // Si curl no existe, también fallará y devolverá ok=false con error_message no vacío
    DeepSeekProvider provider("clave_falsa", "http://127.0.0.1:1", "deepseek-chat");

    LLMRequest request;
    request.system_prompt = "sistema";
    request.user_prompt = "usuario";
    request.max_tokens = 100;
    request.temperature = 0.5;

    LLMResponse response = provider.complete(request);

    // La llamada debe fallar (conexión rechazada o curl ausente)
    CHECK("DeepSeekProvider network error ok == false", response.ok == false);
    CHECK("DeepSeekProvider network error error_message not empty", !response.error_message.empty());
}

int main()
{
    test_fake_provider_interface();
    test_build_deepseek_payload_basic();
    test_build_deepseek_payload_with_params();
    test_parse_deepseek_response_valid();
    test_parse_deepseek_response_error();
    test_parse_deepseek_response_no_choices();
    test_deepseek_provider_name();
    test_deepseek_provider_network_error();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}