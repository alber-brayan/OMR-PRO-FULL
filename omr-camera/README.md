# Lectora OMR Android 2.5

Captura de fichas A4 horizontales, con cuatro esquinas de 6 mm centradas a 7 mm
más dos guías laterales. Gire el teléfono; la captura automática requiere las
cuatro esquinas y una imagen horizontal. La calidad definitiva se evalúa en el
lector de Windows, que también exige las guías laterales.

Compile con JDK 17, Android SDK 35 y `gradlew.bat :app:assembleDebug`. El paquete
es independiente de la versión anterior: `com.lectoraomr.camera.v25`.
Los APK y las claves de firma no se publican; compile localmente.

La selección de grado en la app sólo organiza fotos. El grado de calificación
lo determina el programa de Windows leyendo las burbujas de la ficha. Exportar
el ZIP y cargarlo en OMR 2.5. La captura debe validarse en un teléfono físico.
