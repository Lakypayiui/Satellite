// Tests para InputValidator (FASE 3)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>

#include <json.hpp>
#include "core/validation/InputValidator.h"

using namespace satellite::core::validation;

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

void test_object_properties_required()
{
    nlohmann::json schema = {
        {"type", "object"},
        {"properties", {
            {"a", {{"type", "number"}}},
            {"b", {{"type", "number"}}}
        }},
        {"required", {"a", "b"}}
    };

    std::string err;

    nlohmann::json input1 = {{"a", 1}, {"b", 2}};
    CHECK("object required both present", InputValidator::validate(input1, schema, err));
    CHECK("object required both present err empty", err.empty());

    nlohmann::json input2 = {{"a", 1}};
    CHECK("object missing b", !InputValidator::validate(input2, schema, err));
    CHECK("object missing b err not empty", !err.empty());

    err.clear();
    nlohmann::json input3 = {{"a", "x"}, {"b", 2}};
    CHECK("object wrong type a", !InputValidator::validate(input3, schema, err));
    CHECK("object wrong type a err not empty", !err.empty());

    err.clear();
    nlohmann::json input4 = {};
    CHECK("object empty missing both", !InputValidator::validate(input4, schema, err));
    CHECK("object empty missing both err not empty", !err.empty());

    err.clear();
    nlohmann::json input5 = {{"a", 1.5}, {"b", 2}};
    CHECK("object number accepts decimal", InputValidator::validate(input5, schema, err));
    CHECK("object number accepts decimal err empty", err.empty());

    err.clear();
    nlohmann::json input6 = {{"a", 1}, {"b", 2}, {"c", 3}};
    CHECK("object extra properties allowed", InputValidator::validate(input6, schema, err));
    CHECK("object extra properties allowed err empty", err.empty());
}

void test_number_vs_integer()
{
    nlohmann::json schema_int = {{"type", "integer"}};
    nlohmann::json schema_num = {{"type", "number"}};
    std::string err;

    CHECK("integer accepts 5", InputValidator::validate(5, schema_int, err));
    CHECK("integer accepts 5 err empty", err.empty());

    err.clear();
    CHECK("integer rejects 5.5", !InputValidator::validate(5.5, schema_int, err));
    CHECK("integer rejects 5.5 err not empty", !err.empty());

    err.clear();
    CHECK("number accepts 5", InputValidator::validate(5, schema_num, err));
    CHECK("number accepts 5 err empty", err.empty());

    err.clear();
    CHECK("number accepts 5.5", InputValidator::validate(5.5, schema_num, err));
    CHECK("number accepts 5.5 err empty", err.empty());
}

void test_array_items_minItems()
{
    nlohmann::json schema = {
        {"type", "array"},
        {"items", {{"type", "number"}}},
        {"minItems", 1}
    };
    std::string err;

    CHECK("array valid [1,2,3]", InputValidator::validate(nlohmann::json::array({1, 2, 3}), schema, err));
    CHECK("array valid err empty", err.empty());

    err.clear();
    CHECK("array empty fails minItems", !InputValidator::validate(nlohmann::json::array(), schema, err));
    CHECK("array empty err not empty", !err.empty());

    err.clear();
    CHECK("array invalid item type", !InputValidator::validate(nlohmann::json::array({1, "a"}), schema, err));
    CHECK("array invalid item type err not empty", !err.empty());
}

void test_const_enum()
{
    nlohmann::json schema_const = {{"const", 0}};
    nlohmann::json schema_enum = {{"enum", {1, 2, 3}}};
    std::string err;

    CHECK("const 0 matches", InputValidator::validate(0, schema_const, err));
    CHECK("const 0 matches err empty", err.empty());

    err.clear();
    CHECK("const 1 fails", !InputValidator::validate(1, schema_const, err));
    CHECK("const 1 fails err not empty", !err.empty());

    err.clear();
    CHECK("enum 2 matches", InputValidator::validate(2, schema_enum, err));
    CHECK("enum 2 matches err empty", err.empty());

    err.clear();
    CHECK("enum 5 fails", !InputValidator::validate(5, schema_enum, err));
    CHECK("enum 5 fails err not empty", !err.empty());
}

void test_not()
{
    nlohmann::json schema = {{"not", {{"const", 0}}}};
    std::string err;

    CHECK("not const 0: 0 fails", !InputValidator::validate(0, schema, err));
    CHECK("not const 0: 0 fails err not empty", !err.empty());

    err.clear();
    CHECK("not const 0: -3 passes", InputValidator::validate(-3, schema, err));
    CHECK("not const 0: -3 passes err empty", err.empty());

    err.clear();
    CHECK("not const 0: 2.5 passes", InputValidator::validate(2.5, schema, err));
    CHECK("not const 0: 2.5 passes err empty", err.empty());
}

void test_numeric_bounds()
{
    nlohmann::json schema = {
        {"type", "number"},
        {"minimum", 0},
        {"exclusiveMaximum", 10}
    };
    std::string err;

    CHECK("minimum 0 inclusive: 0 passes", InputValidator::validate(0, schema, err));
    CHECK("minimum 0 inclusive: 0 passes err empty", err.empty());

    err.clear();
    CHECK("exclusiveMaximum 10: 10 fails", !InputValidator::validate(10, schema, err));
    CHECK("exclusiveMaximum 10: 10 fails err not empty", !err.empty());

    err.clear();
    CHECK("exclusiveMaximum 10: 9.99 passes", InputValidator::validate(9.99, schema, err));
    CHECK("exclusiveMaximum 10: 9.99 passes err empty", err.empty());

    err.clear();
    CHECK("minimum 0: -1 fails", !InputValidator::validate(-1, schema, err));
    CHECK("minimum 0: -1 fails err not empty", !err.empty());

    err.clear();
    nlohmann::json schema_excl_min = {
        {"type", "number"},
        {"exclusiveMinimum", 0}
    };
    CHECK("exclusiveMinimum 0: 0 fails", !InputValidator::validate(0, schema_excl_min, err));
    CHECK("exclusiveMinimum 0: 0 fails err not empty", !err.empty());

    err.clear();
    CHECK("exclusiveMinimum 0: 1 passes", InputValidator::validate(1, schema_excl_min, err));
    CHECK("exclusiveMinimum 0: 1 passes err empty", err.empty());
}

void test_no_type_in_schema()
{
    nlohmann::json schema = {};
    std::string err;

    CHECK("empty schema: 42 passes", InputValidator::validate(42, schema, err));
    CHECK("empty schema: 42 passes err empty", err.empty());

    err.clear();
    CHECK("empty schema: \"hola\" passes", InputValidator::validate("hola", schema, err));
    CHECK("empty schema: \"hola\" passes err empty", err.empty());

    err.clear();
    CHECK("empty schema: {} passes", InputValidator::validate(nlohmann::json::object(), schema, err));
    CHECK("empty schema: {} passes err empty", err.empty());
}

void test_string_type()
{
    nlohmann::json schema = {{"type", "string"}};
    std::string err;

    CHECK("string type: \"hola\" passes", InputValidator::validate("hola", schema, err));
    CHECK("string type: \"hola\" passes err empty", err.empty());

    err.clear();
    CHECK("string type: 42 fails", !InputValidator::validate(42, schema, err));
    CHECK("string type: 42 fails err not empty", !err.empty());
}

void test_nesting()
{
    nlohmann::json schema = {
        {"type", "object"},
        {"properties", {
            {"inner", {
                {"type", "object"},
                {"properties", {
                    {"x", {{"type", "number"}}}
                }},
                {"required", {"x"}}
            }}
        }},
        {"required", {"inner"}}
    };
    std::string err;

    nlohmann::json input1 = {{"inner", {{"x", 1}}}};
    CHECK("nested object valid", InputValidator::validate(input1, schema, err));
    CHECK("nested object valid err empty", err.empty());

    err.clear();
    nlohmann::json input2 = {{"inner", {}}};
    CHECK("nested object missing required inner.x", !InputValidator::validate(input2, schema, err));
    CHECK("nested object missing required err not empty", !err.empty());
}

void test_error_message_non_empty_on_failure()
{
    nlohmann::json schema = {{"type", "number"}};
    std::string err;

    CHECK("error_message not empty on type mismatch", !InputValidator::validate("not a number", schema, err));
    CHECK("error_message content not empty", !err.empty());
}

int main()
{
    test_object_properties_required();
    test_number_vs_integer();
    test_array_items_minItems();
    test_const_enum();
    test_not();
    test_numeric_bounds();
    test_no_type_in_schema();
    test_string_type();
    test_nesting();
    test_error_message_non_empty_on_failure();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}