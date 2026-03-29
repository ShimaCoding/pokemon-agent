# Plan de Pruebas: Pokémon MCP Server

Este documento contiene un conjunto de pruebas diseñadas para demostrar las capacidades del Pokémon MCP Server. Cada sección se enfoca en una herramienta específica y proporciona casos de prueba con el prompt esperado y el resultado típico.

## 1. Búsqueda de Pokémon (`search_pokemon`)

Esta herramienta permite explorar la lista global de Pokémon con paginación.

| ID | Caso de Prueba | Prompt de Ejemplo | Resultado Esperado |
|:---|:---|:---|:---|
| BT-01 | Listado inicial | "Muéstrame los primeros 5 Pokémon" | Una lista con Bulbasaur, Ivysaur, Venusaur, Charmander, Charmeleon. |
| BT-02 | Paginación (Offset) | "Dime quién es el Pokémon número 25 según la lista" | Identificación de Pikachu (#25). |
| BT-03 | Límite personalizado | "Dame una lista de 10 Pokémon" | Lista compacta de 10 entradas. |

## 2. Información Detallada (`get_pokemon_info`)

Obtiene datos técnicos, tipos, habilidades y estadísticas base de un Pokémon específico.

| ID | Caso de Prueba | Prompt de Ejemplo | Resultado Esperado |
|:---|:---|:---|:---|
| ID-01 | Por Nombre | "Dame información de Mewtwo" | Altura, peso, tipos (Psíquico), habilidades y stats base. |
| ID-02 | Por Número (ID) | "Qué Pokémon es el #150?" | Información detallada de Mewtwo. |
| ID-03 | Tipos y Habilidades | "Cuáles son las habilidades de Gengar?" | Lista de habilidades (Cursed Body) e información de tipos. |

## 3. Análisis de Estadísticas (`analyze_pokemon_stats`)

Proporciona una visión analítica del potencial de combate basándose en los stats base.

| ID | Caso de Prueba | Prompt de Ejemplo | Resultado Esperado |
|:---|:---|:---|:---|
| AS-01 | Análisis de Tanque | "Analiza las estadísticas de Shuckle" | Identificación de Defensa y Defensa Especial extremadamente altas. |
| AS-02 | Análisis de Velocista | "Qué tan rápido es Jolteon comparado con sus otros stats?" | Gráfico de barras o desglose resaltando su alta Velocidad. |
| AS-03 | Rating General | "Dame un resumen del potencial de Rayquaza" | Rating alto, desglose de ataque/ataque especial dominante. |

## 4. Efectividad de Tipos (`get_type_effectiveness`)

Consulta las debilidades y fortalezas elementales.

| ID | Caso de Prueba | Prompt de Ejemplo | Resultado Esperado |
|:---|:---|:---|:---|
| ET-01 | Ventaja Ofensiva | "Contra qué tipos es fuerte el tipo Agua?" | Fuego, Tierra, Roca. |
| ET-02 | Resistencia/Debilidad | "Qué tipos resisten el tipo Eléctrico?" | Eléctrico, Planta, Dragón (y Tierra tiene inmunidad). |
| ET-03 | Inmunidades | "Qué tipo no recibe daño de ataques tipo Fantasma?" | Identificación del tipo Normal. |

---

## 5. Escenarios Combinados (Capacidad del Agente)

El agente puede usar múltiples herramientas para responder preguntas complejas.

| ID | Caso de Prueba | Prompt de Ejemplo | Flujo del Agente |
|:---|:---|:---|:---|
| EC-01 | Comparativa Estratégica | "¿Quién tiene mejor ataque: Arcanine o Gyarados? Analiza a ambos." | `analyze_pokemon_stats` para ambos y comparación directa. |
| EC-02 | Análisis de Equipo | "Tengo un equipo débil al tipo Hielo. ¿Qué tipo debería buscar para contrarrestarlo y qué Pokémon me recomiendas?" | `get_type_effectiveness` (Hielo) + Sugerencia (Fuego/Acero) + `search_pokemon`. |
| EC-03 | Ficha Técnica Completa | "Hazme un reporte completo de Lucario incluyendo su efectividad de tipos." | `get_pokemon_info` + `analyze_pokemon_stats` + `get_type_effectiveness` (Acero/Lucha). |

---

## 6. MCP Prompts Integrados

El servidor MCP ofrece prompts preconfigurados que puedes usar directamente escribiendo su nombre o invocándolos desde la interfaz del agente.

| Prompt | Descripción | Uso sugerido |
|:---|:---|:---|
| `pokedex-entry` | Genera una entrada estilo Pokédex clásica. | "Usa el prompt pokedex-entry para Garchomp" |
| `battle-strategy` | Analiza debilidades y sugiere una estrategia de combate. | "Aplica battle-strategy contra un equipo de tipo Agua" |
| `stat-comparison` | Compara las estadísticas de dos Pokémon visualmente. | "Ejecuta stat-comparison entre Dragonite y Salamence" |

## 7. Recursos Disponibles (Resources)

El servidor expone recursos estáticos que el agente puede leer para contextualizar sus respuestas.

- **`pokemon://types/chart`**: La tabla completa de multiplicadores de daño entre todos los tipos.
- **`pokemon://abilities/list`**: Un índice de habilidades comunes y sus efectos.
- **`pokemon://stats/averages`**: Datos sobre el promedio de estadísticas base por generación.

---

## Cómo Ejecutar estas Pruebas

1. **Copia el prompt de ejemplo** de las tablas anteriores.
2. **Pégalo en el chat** del agente.
3. **Observa el panel de trazas (Tool Trace)** para verificar qué herramientas de `pokemon-mcp-s` se están invocando y qué datos devuelven en crudo.
