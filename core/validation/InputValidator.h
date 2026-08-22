#pragma once

// Validador de subconjunto de JSON Schema (Determinista, sin dependencias externas).
// Keywords soportados:
//   - "type": "object" | "number" | "integer" | "string" | "boolean" | "array" | "null"
//     (number acepta enteros; integer NO acepta 1.5; sin type → no hay chequeo de tipo)
//   - "properties" (objeto: validar cada propiedad recursivamente)
//   - "required" (array de strings)
//   - "items" (schema único aplicado a TODOS los elementos del array)
//   - "minItems" (entero >= 0)
//   - "const" (igualdad exacta)
//   - "enum" (array de valores permitidos)
//   - "not" (negar la validación del subschema: validar hijo, invertir)
//   - "minimum" (número), "maximum" (número)
//   - "exclusiveMinimum" (número, > estricto), "exclusiveMaximum" (número, < estricto)
// Keywords desconocidos se IGNORAN (no causan error ni validación).
// Recursivo, determinista, sin estado. error_message en inglés describe la PRIMERA falla.

#include <json.hpp>
#include <string>

namespace satellite::core::validation
{

class InputValidator
{
public:
    static bool validate(const nlohmann::json& input, const nlohmann::json& schema, std::string& error_message);

private:
    static bool validate_type(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_const(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_enum(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_not(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_number_range(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_object(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_array(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
    static bool validate_recursive(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path);
};

} // namespace satellite::core::validation