# Reporte Excel OMR 2.5

`excel25.export_report(records, output_path=None, template_path=None,
course="Matematica", scoring=None)` devuelve bytes XLSX. Si se proporciona
`output_path`, guarda de forma atómica y devuelve un `Path`.

La plantilla de la editorial queda en `assets/report_template.xlsx`; es una
copia exacta del archivo de referencia. No se sobrescribe. El exportador mantiene
sus estilos, anchos, altos de fila, gráfico de cabecera, cuadros de texto y
configuración de impresión usando el paquete OOXML directamente. No requiere
instalar Excel ni Node para exportar.

## Datos y hojas

Siempre se crean seis hojas: `Grado 1` a `Grado 6`. La columna D de la referencia,
antes `LUGAR`, pasa a `Nº ORDEN` para el campo manual de la nueva ficha. Las otras
ocho columnas conservan su función. El rótulo gráfico de grado se actualiza en
cada hoja; la imagen de marca permanece en su archivo original.

Solo se incluyen registros con `confirmed is True`, nombre, colegio, grado válido,
15 respuestas (`A`–`E`, `-` blanco, `X` doble), buenas, malas, blancos y puntaje.
Los registros confirmados incompletos o inconsistentes provocan `ValueError`.
Las dobles marcas forman parte de las malas; buenas + malas + blancos suma 15.
Los alumnos de muestra de la plantilla y sus cadenas compartidas se eliminan.

Campos esperados por registro:

```python
{
    "name": "NOMBRE CONFIRMADO", "school": "COLEGIO", "grade": 2,
    "answers": ["A"] * 15,
    "correct": 15, "incorrect": 0, "blank": 0, "double": 0,
    "score": 150, "order": "001", "time": "08:30", "confirmed": True
}
```

Este ejemplo es ilustrativo, no un resultado real de lectura.

## Cálculo y orden

Por defecto se aplica la regla de la referencia: buena +10, mala -1 y blanco 0.
`scoring` admite las claves `buenas`, `malas`, `blanco` (también `correct`,
`incorrect`, `blank`). Se verifica que el puntaje confirmado coincida con los
coeficientes. Las fórmulas nativas de puntaje y puesto incluyen valores calculados
en caché para lectores que no recalculan al abrir.

Se ordena por puntaje descendente, luego por hora conocida ascendente; las horas
ausentes quedan después de las conocidas. Si ambos criterios coinciden, el puesto
es compartido (1, 1, 3). El nombre sirve solo para ordenar de forma estable esos
empates. Las fórmulas de puesto se actualizan si se editan puntuación/hora, aunque
Excel no reordena físicamente las filas de manera automática.

Orden se almacena como texto para conservar ceros iniciales. Hora usa un valor
numérico de Excel con el formato horario de la plantilla. Un campo ausente queda
vacío; no se transforma en cero. Nombres, colegios y órdenes se escriben como
cadenas literales, incluso si comienzan por `=`, `+`, `-` o `@`.

La plantilla dispone de 70 filas; el exportador prolonga los estilos si se
supera esa cantidad y amplía el área de impresión sin truncar alumnos. Las filas
de cabecera se repiten al imprimir.

## Verificación

```powershell
python -X utf8 -m unittest tests.test_excel25 -v
```

Las pruebas cubren las seis hojas, plantilla intacta, estilos y dibujo de marca,
85 alumnos, datos faltantes, dobles marcas, puntajes y horas, empates, protección
contra fórmulas introducidas como texto y salida a archivo.

El reporte de demostración usado para inspección visual se renderizó con Artifact
Tool. Las seis cabeceras se revisaron; además se modificaron temporalmente buenas,
malas y hora en memoria para comprobar que puntuación y puesto se recalculan.
Las pruebas no verifican comportamiento de una instalación particular de Excel.

No vuelva a guardar estos paquetes con una biblioteca que descarte cuadros de
texto de DrawingML: se perdería parte de la cabecera de la editorial.
