// Proyecto A de la prueba de portabilidad (FASE 24): proyecto C++.

#include <vector>
#include <string>

namespace app
{

double calcular_promedio(const std::vector<double>& valores)
{
    double suma = 0.0;
    for (double v : valores)
    {
        suma += v;
    }
    return valores.empty() ? 0.0 : suma / static_cast<double>(valores.size());
}

int contar_vocales(const std::string& texto)
{
    int n = 0;
    for (char c : texto)
    {
        if (c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u')
        {
            ++n;
        }
    }
    return n;
}

} // namespace app

int main()
{
    return 0;
}
