# VEXgraph — manual de uso

*(Also available in English: [manual.md](manual.md).)*

VEXgraph es un traductor entre dos maneras de escribir el mismo
wrangle: código VEX y un grafo de nodos. Edita el lado que mejor leas —
monta nodos y mira cómo el código se escribe solo, o teclea código y
mira cómo se convierte en nodos. Ninguno de los dos lados es una vista
previa del otro; los dos son la cosa real, y todo lo que ves en el
panel de código ha pasado por el propio compilador de Houdini antes de
llegar a tus ojos.

Para lo que pasa bajo el capó — y las ideas que solo existen en esta
herramienta — lee [how-vexgraph-thinks.md](how-vexgraph-thinks.md)
(en inglés).

## El panel, de izquierda a derecha

- **Librería** — todos los nodos, por categorías. Arrastra uno, o
  ignora esta columna y usa la búsqueda con Tab.
- **Canvas** — el grafo. La cadena de flechas blancas es el *orden de
  ejecución*: VEX corre de arriba abajo, y esa cadena es el
  arriba-abajo.
- **Panel de código** — el VEX generado, en vivo. Es editable: teclea o
  pega cualquier VEX y pulsa **Ctrl+Enter** para construir los nodos.
- **Problems** — lo que esté mal, en palabras llanas, cada entrada
  nombrando al nodo culpable. Clicarla selecciona ese nodo.
- **Asistente** — pide un grafo con tus palabras, o pregunta qué hace
  el que tienes delante.

## Los primeros cinco minutos

1. Pulsa **Tab** sobre el canvas y escribe lo que buscas ("noise",
   "distance", "set color"). Elige un nodo.
2. Arrastra de un punto de salida a uno de entrada para cablear
   valores. Un cable sin sentido (un vector a un contador) se rechaza
   con una explicación en la línea de estado.
3. Los valores sin cable se editan en el propio nodo, en sus filas.
4. Mira cómo el panel de código escribe el wrangle mientras trabajas.
   Selecciona un nodo y sus líneas se iluminan; clica una línea y se
   selecciona su nodo.
5. **Live** está activado por defecto: el VEX aterriza en el wrangle
   mientras editas, y el viewport te sigue. Un grafo con errores no se
   escribe nunca — el último código sano se queda hasta que el nuevo
   sea válido.

## Herramientas del día a día

| Acción | Cómo |
| --- | --- |
| Buscar/añadir un nodo | **Tab**, o clic derecho ▸ *Add node...* |
| Encuadrarlo todo | **F**, o el botón *Frame* |
| Reordenar el grafo | botón *Tidy* |
| Borrar la selección | **Supr** / **Retroceso**, o clic derecho |
| Deshacer / rehacer | **Ctrl+Z** / **Ctrl+Y** |
| Copiar, cortar, pegar nodos | **Ctrl+C / X / V** |
| Panear | arrastra con botón derecho o central, o **Alt**+arrastrar |
| Menú contextual | clic derecho y soltar sin mover |
| Construir nodos desde el código | **Ctrl+Enter** en el panel de código |
| Ayuda de Houdini del nodo | doble clic en el nodo |
| Cancelar un cable a medias | clic derecho, o **Escape** |

**Snippets** abre todas las expresiones ya hechas instaladas en esta
máquina (las de Houdini, las de OD Tools y las tuyas) como nodos que
puedes leer y cambiar. **Save as Snippet** guarda tu grafo actual en
esa misma lista, con un nombre que además se muestra en el canvas. Tus
snippets aparecen también en el menú nativo de presets del Attribute
Wrangle.

## Tipos y colores

Cada cable lleva un tipo de VEX, y cada punto va coloreado por él. Un
socket cuyo tipo lo decide un desplegable (el *Type* de Add, de Get
Attribute...) muestra el tipo elegido, nunca un comodín. Cuando un
float se encuentra con un int, la conversión se muestra como un nodo de
verdad (Round to Whole) en vez de ocurrir en silencio — el mismo
significado que en VEX, pero visible.

## Vectores y pins de componente

Cualquier salida vectorial ofrece sus partes como pins propios:
**doble clic en una salida vectorial** muestra `.x .y .z` (y `.w` en un
vector4) como pequeños pins float. Leer una parte de un vector es solo
un cable — sin caja Split Vector — y el código dice `@P.y`, exactamente
como lo teclearía una persona. El nodo Split Vector sigue existiendo
para quien prefiera la caja.

## Trabajar con las partes de un vector

Tres preguntas salen enseguida, y tienen respuestas cortas.

**"¿Cómo leo una parte?"** Doble clic en una salida vectorial: le
aparecen pins `.x .y .z` (y `.w` en un vector4). *Ese* es el nodo que da
tres salidas — es la propia salida, partida. Cablea el pin que quieras.

**"¿Cómo escribo una parte?"** Set Component escribe exactamente una y
deja las otras como estaban: `@P.y = 0;`.

**"¿Cómo escribo las tres?"** Ni con tres Make Vector ni sumando nada.
Dos caminos, ambos de dos nodos:

- **Reemplazarlas todas**: Make Vector (x, y, z) hacia Set Attribute.
  Escribe `@P = set(x, y, z);` — los valores antiguos desaparecen, que
  es lo que quieres cuando construyes una posición o un color de cero.
- **Cambiarlas de una en una**: tres nodos Set Component en la cadena de
  ejecución, uno para x, otro para y, otro para z. Escribe tres líneas,
  cada una modificando el atributo en el sitio. No hay que sumar nada:
  se ejecutan en orden y cada una ve lo que dejó la anterior.

Lo único que *no* funciona es cablear un Make Vector a un Set Component:
un componente es un solo número, así que ese socket admite un float. Si
te ves queriendo hacerlo, lo que querías era Set Attribute.

## Favoritos

Clic derecho en cualquier nodo — en el canvas o en la librería — y
márcalo con estrella. Los favoritos salen primero en la búsqueda de Tab
(marcados con ★) y tienen su propio estante arriba de la librería; el
botón ★ junto al buscador muestra solo esos. La librería trae 1360
nodos y nadie usa más de un par de docenas: aquí dices cuáles.

## Canales

`chf("scale")` es un spinner del wrangle, no un cálculo, así que una
lectura de canal vive escrita *dentro* del input que la usa — teclea
`chf("size")` directamente en la fila de un nodo. Solo un canal cuyo
*nombre* se calcula necesita nodo.

## Bucles

- **Repeat** — un número fijo de pasadas, con el número de pasada como
  salida que solo existe dentro del cuerpo.
- **For Each** — una vez por elemento de una lista, con el elemento y
  su posición como salidas.
- **While** — mientras una condición siga siendo cierta. Algo dentro
  del bucle debe cambiar la condición, o no acaba nunca. Una condición
  que *hace* algo cada vez que se consulta (como `pciterate()`) no
  puede ser un cable y se queda como Inline VEX — el manual de ideas
  explica por qué.
- **Break If / Skip If** — salir del bucle, o saltar a la siguiente
  pasada.

## Funciones

Un grafo puede definir funciones y llamarlas — el equivalente en este
editor a los subnets de VOPs, salvo que son funciones VEX de verdad.

- **Importar** código que define funciones (`int drawLine(...) {...}`)
  le da a cada función su propio grafo interior; las llamadas aparecen
  como nodos.
- **Doble clic en un nodo de llamada** para entrar. Sobre el canvas
  aparece una barrita con la firma de la función; su botón es el camino
  de vuelta. El panel de código sigue mostrando el documento completo
  mientras estás dentro, con sus highlights.
- **Colapsar**: selecciona unos nodos, clic derecho ▸ *Collapse into a
  function...*, ponle nombre. Lo que alimentaba la selección se vuelve
  parámetros; el valor que salía se vuelve el retorno; la selección se
  vuelve una llamada.

El colapso rehúsa con educación — y lo deja todo exactamente como
estaba — cuando la selección no puede ser honestamente una función:

- **Escribe un atributo.** `@Cd = ...` solo significa algo en el cuerpo
  principal; las funciones VEX no pueden tocar los `@`. (Las *lecturas*
  de atributos sí valen: se quedan fuera calladamente y sus valores
  llegan como parámetros.)
- **Produce más de un valor usado fuera.** Una función devuelve una
  cosa.
- **Su valor lo usan varios pasos.** Una llamada ocurre una vez; la
  expresión que sustituye se recomponía en cada uso, y un cambio entre
  dos usos se perdería.
- **Su valor gobierna la condición de un While**, que se re-comprueba
  en cada vuelta — ninguna llamada hecha una vez puede hacer eso.
  Colapsa el bucle entero en su lugar.
- **Hay un bucle o rama seleccionados sin su cuerpo completo.**
- **El nombre está cogido**, es una palabra clave o un tipo de VEX, o
  lleva caracteres que un identificador VEX no admite (acentos
  incluidos).

## Inline VEX — la escotilla de escape

Todo lo que el importador no puede expresar como nodos se conserva,
byte a byte, en un nodo Inline VEX que corre como cualquier otro paso.
Nunca se pierde ni se rechaza nada: la promesa es *no fallar jamás una
importación*, degradar a texto en su lugar. La lista de Problems dice
exactamente qué se quedó como texto y por qué. Los `do...while` y algún
otro rincón viven ahí hoy.

## El asistente

Pide con tus palabras — "empuja los puntos por sus normales, más arriba
del todo" — y elige si la respuesta llega como nodos o como
explicación. Los modelos locales corren en tu propia máquina (y se
descargan de la memoria solos cuando llevan un rato sin usarse); las
respuestas que construyen grafos pasan por el mismo importador y los
mismos checks de compilador que todo lo demás.

## Cuando algo pinta mal

- **Un nodo tiene un aro naranja o rojo** — la lista de Problems tiene
  una frase llana sobre él.
- **El panel de código muestra código viejo** — se regenera un momento
  después de que dejes de editar; el check *Live* controla si aterriza
  en el wrangle.
- **Un cable no quiere conectar** — la línea de estado dice por qué, y
  normalmente qué nodo lo arreglaría (Length, Make Array...).
- **Tras Ctrl+Enter el grafo sale recolocado** — construir desde código
  ordena los nodos de cero; lo que se preserva es el código.
