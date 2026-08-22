#include "InputValidator.h"

namespace satellite::core::validation
{

bool InputValidator::validate(const nlohmann::json& input, const nlohmann::json& schema, std::string& error_message)
{
    return validate_recursive(input, schema, error_message, "");
}

bool InputValidator::validate_recursive(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!validate_type(value, schema, error_message, path))
    {
        return false;
    }

    if (!validate_const(value, schema, error_message, path))
    {
        return false;
    }

    if (!validate_enum(value, schema, error_message, path))
    {
        return false;
    }

    if (!validate_not(value, schema, error_message, path))
    {
        return false;
    }

    if (!validate_number_range(value, schema, error_message, path))
    {
        return false;
    }

    if (!validate_object(value, schema, error_message, path))
    {
        return false;
    }

    if (!validate_array(value, schema, error_message, path))
    {
        return false;
    }

    return true;
}

bool InputValidator::validate_type(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!schema.contains("type"))
    {
        return true;
    }

    const auto& type_val = schema["type"];
    if (!type_val.is_string())
    {
        return true;
    }

    const std::string type = type_val.get<std::string>();
    std::string type_name;

    if (type == "object")
    {
        if (!value.is_object())
        {
            type_name = value.is_null() ? "null" :
                        value.is_boolean() ? "boolean" :
                        value.is_number_integer() ? "integer" :
                        value.is_number_float() ? "number" :
                        value.is_string() ? "string" :
                        value.is_array() ? "array" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type object, got " + type_name;
            return false;
        }
    }
    else if (type == "number")
    {
        if (!value.is_number())
        {
            type_name = value.is_null() ? "null" :
                        value.is_boolean() ? "boolean" :
                        value.is_string() ? "string" :
                        value.is_array() ? "array" :
                        value.is_object() ? "object" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type number, got " + type_name;
            return false;
        }
    }
    else if (type == "integer")
    {
        if (!value.is_number_integer())
        {
            type_name = value.is_null() ? "null" :
                        value.is_boolean() ? "boolean" :
                        value.is_number_float() ? "number" :
                        value.is_string() ? "string" :
                        value.is_array() ? "array" :
                        value.is_object() ? "object" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type integer, got " + type_name;
            return false;
        }
    }
    else if (type == "string")
    {
        if (!value.is_string())
        {
            type_name = value.is_null() ? "null" :
                        value.is_boolean() ? "boolean" :
                        value.is_number() ? "number" :
                        value.is_array() ? "array" :
                        value.is_object() ? "object" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type string, got " + type_name;
            return false;
        }
    }
    else if (type == "boolean")
    {
        if (!value.is_boolean())
        {
            type_name = value.is_null() ? "null" :
                        value.is_number() ? "number" :
                        value.is_string() ? "string" :
                        value.is_array() ? "array" :
                        value.is_object() ? "object" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type boolean, got " + type_name;
            return false;
        }
    }
    else if (type == "array")
    {
        if (!value.is_array())
        {
            type_name = value.is_null() ? "null" :
                        value.is_boolean() ? "boolean" :
                        value.is_number() ? "number" :
                        value.is_string() ? "string" :
                        value.is_object() ? "object" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type array, got " + type_name;
            return false;
        }
    }
    else if (type == "null")
    {
        if (!value.is_null())
        {
            type_name = value.is_boolean() ? "boolean" :
                        value.is_number() ? "number" :
                        value.is_string() ? "string" :
                        value.is_array() ? "array" :
                        value.is_object() ? "object" : "unknown";
            error_message = (path.empty() ? "root" : path) + ": expected type null, got " + type_name;
            return false;
        }
    }

    return true;
}

bool InputValidator::validate_const(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!schema.contains("const"))
    {
        return true;
    }

    const auto& const_val = schema["const"];
    if (value != const_val)
    {
        error_message = (path.empty() ? "root" : path) + ": const validation failed";
        return false;
    }

    return true;
}

bool InputValidator::validate_enum(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!schema.contains("enum"))
    {
        return true;
    }

    const auto& enum_val = schema["enum"];
    if (!enum_val.is_array())
    {
        return true;
    }

    for (const auto& allowed : enum_val)
    {
        if (value == allowed)
        {
            return true;
        }
    }

    error_message = (path.empty() ? "root" : path) + ": enum validation failed";
    return false;
}

bool InputValidator::validate_not(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!schema.contains("not"))
    {
        return true;
    }

    const auto& not_schema = schema["not"];
    std::string dummy_error;
    bool child_valid = validate_recursive(value, not_schema, dummy_error, path);

    if (child_valid)
    {
        error_message = (path.empty() ? "root" : path) + ": not validation failed";
        return false;
    }

    return true;
}

bool InputValidator::validate_number_range(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!value.is_number())
    {
        return true;
    }

    double num = value.get<double>();

    if (schema.contains("minimum"))
    {
        double min = schema["minimum"].get<double>();
        if (num < min)
        {
            error_message = (path.empty() ? "root" : path) + ": minimum " + std::to_string(min) + " violated";
            return false;
        }
    }

    if (schema.contains("maximum"))
    {
        double max = schema["maximum"].get<double>();
        if (num > max)
        {
            error_message = (path.empty() ? "root" : path) + ": maximum " + std::to_string(max) + " violated";
            return false;
        }
    }

    if (schema.contains("exclusiveMinimum"))
    {
        double ex_min = schema["exclusiveMinimum"].get<double>();
        if (num <= ex_min)
        {
            error_message = (path.empty() ? "root" : path) + ": exclusiveMinimum " + std::to_string(ex_min) + " violated";
            return false;
        }
    }

    if (schema.contains("exclusiveMaximum"))
    {
        double ex_max = schema["exclusiveMaximum"].get<double>();
        if (num >= ex_max)
        {
            error_message = (path.empty() ? "root" : path) + ": exclusiveMaximum " + std::to_string(ex_max) + " violated";
            return false;
        }
    }

    return true;
}

bool InputValidator::validate_object(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!value.is_object())
    {
        return true;
    }

    if (schema.contains("required"))
    {
        const auto& required = schema["required"];
        if (required.is_array())
        {
            for (const auto& req : required)
            {
                if (req.is_string())
                {
                    const std::string& prop_name = req.get<std::string>();
                    if (!value.contains(prop_name))
                    {
                        error_message = (path.empty() ? "root" : path) + ": missing required property '" + prop_name + "'";
                        return false;
                    }
                }
            }
        }
    }

    if (schema.contains("properties"))
    {
        const auto& properties = schema["properties"];
        if (properties.is_object())
        {
            for (auto it = properties.begin(); it != properties.end(); ++it)
            {
                const std::string& prop_name = it.key();
                const auto& prop_schema = it.value();

                if (value.contains(prop_name))
                {
                    std::string new_path = path.empty() ? prop_name : path + "." + prop_name;
                    if (!validate_recursive(value[prop_name], prop_schema, error_message, new_path))
                    {
                        return false;
                    }
                }
            }
        }
    }

    return true;
}

bool InputValidator::validate_array(const nlohmann::json& value, const nlohmann::json& schema, std::string& error_message, const std::string& path)
{
    if (!value.is_array())
    {
        return true;
    }

    if (schema.contains("minItems"))
    {
        const auto& min_items_val = schema["minItems"];
        if (min_items_val.is_number_integer())
        {
            std::size_t min_items = static_cast<std::size_t>(min_items_val.get<int64_t>());
            if (value.size() < min_items)
            {
                error_message = (path.empty() ? "root" : path) + ": minItems " + std::to_string(min_items) + " violated";
                return false;
            }
        }
    }

    if (schema.contains("items"))
    {
        const auto& items_schema = schema["items"];
        std::size_t index = 0;
        for (const auto& element : value)
        {
            std::string new_path = (path.empty() ? "" : path + ".") + "[" + std::to_string(index) + "]";
            if (!validate_recursive(element, items_schema, error_message, new_path))
            {
                return false;
            }
            ++index;
        }
    }

    return true;
}

} // namespace satellite::core::validation