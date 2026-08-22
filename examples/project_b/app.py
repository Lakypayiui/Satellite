# Proyecto B de la prueba de portabilidad (FASE 24): proyecto Python.


def calcular_promedio(valores):
    """Devuelve el promedio de una lista de números."""
    if not valores:
        return 0.0
    return sum(valores) / len(valores)


def contar_vocales(texto):
    """Cuenta las vocales de un texto."""
    vocales = "aeiou"
    return sum(1 for c in texto.lower() if c in vocales)


class Procesador:
    """Procesador de texto de ejemplo."""

    def __init__(self, prefijo):
        self.prefijo = prefijo

    def procesar(self, texto):
        return self.prefijo + texto


def main():
    print(calcular_promedio([1, 2, 3, 4]))
    print(contar_vocales("hola mundo"))


if __name__ == "__main__":
    main()
