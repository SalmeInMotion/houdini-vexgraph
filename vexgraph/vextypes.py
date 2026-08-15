"""VEX's type system, reduced to what a node graph needs to police connections.

The point of a typed graph is that a wrong wire is impossible to draw, not that
it fails later in the compiler. So the rules here are deliberately stricter than
VEX itself in one place: VEX will silently truncate a float assigned to an int,
which is exactly the kind of quiet wrongness a visual tool exists to prevent.
Narrowing has to go through a Round / Floor / Truncate node, where the user can
see the decision being made.

Widening the other way (int to float, scalar splat to a vector) is safe, means
what it looks like, and is left implicit.
"""

from __future__ import annotations

# Every type a socket may carry. Arrays are spelled with a trailing "[]", the
# same way VEX declares them, so a type name can be shown to the user as-is.
SCALAR_TYPES = ("int", "float")
VECTOR_TYPES = {"vector2": 2, "vector": 3, "vector4": 4}
MATRIX_TYPES = {"matrix2": 4, "matrix3": 9, "matrix": 16}
OTHER_TYPES = ("string", "dict")

BASE_TYPES = (
    *SCALAR_TYPES,
    *VECTOR_TYPES,
    *MATRIX_TYPES,
    *OTHER_TYPES,
)

ARRAY_TYPES = tuple(f"{t}[]" for t in BASE_TYPES)

# A placeholder for sockets whose real type is decided per node instance by a
# parameter, such as the Type dropdown on Get Attribute. It is only ever valid
# inside a definition: `Graph.socket_type` resolves it, and refuses to hand one
# back, so "any" can never reach the emitter or a connection check.
ANY = "any"
# "a list of whatever that param says", so one For Each node handles every
# element type instead of there being one per type.
ANY_ARRAY = "any[]"

ALL_TYPES = (*BASE_TYPES, *ARRAY_TYPES, ANY, ANY_ARRAY)

# How a wrangle spells each type in an @attribute binding.
ATTR_PREFIX = {
    "float": "f", "int": "i", "vector2": "u", "vector": "v", "vector4": "p",
    "matrix2": "2", "matrix3": "3", "matrix": "4", "string": "s", "dict": "d",
}


def declaration(vex_type: str, name: str) -> str:
    """How VEX spells declaring `name` of `vex_type`.

    Arrays put their brackets after the variable, not after the type, and
    getting it the wrong way round is the single most common VEX syntax error.
    """
    if is_array(vex_type):
        return f"{element_type(vex_type)} {name}[]"
    return f"{vex_type} {name}"


def attribute_reference(name: str, vex_type: str) -> str:
    """`@P`, `v@up`, `s@name` - the binding a wrangle understands.

    P, N, Cd and the rest of the standard set are typed by Houdini already, so
    a prefix on them is noise; anything else needs one or it binds as a float.
    """
    if name in _IMPLICITLY_TYPED:
        return f"@{name}"
    return f"{ATTR_PREFIX.get(vex_type, 'f')}@{name}"


# Attributes the Wrangle SOP types on its own.
_IMPLICITLY_TYPED = {
    "P", "N", "Cd", "v", "up", "scale", "rest", "uv", "accel", "force",
    "targetv", "torque", "orient", "rot", "pscale", "width", "Alpha",
    "density", "age", "life", "mass", "drag", "bounce", "friction",
    "id", "nextid", "name", "instance", "shop_materialpath",
    "ptnum", "numpt", "primnum", "numprim", "vtxnum", "numvtx",
    "elemnum", "numelem", "Frame", "Time", "TimeInc", "SimFrame", "SimTime",
    "group", "ptgroup", "primgroup",
}

# Zero value per type, used when an input socket is left unconnected and the
# node definition gives no default of its own.
ZERO = {
    "int": "0",
    "float": "0",
    "vector2": "{0, 0}",
    "vector": "{0, 0, 0}",
    "vector4": "{0, 0, 0, 0}",
    # Not `ident()`: that is itself overloaded on its return type, so it makes
    # any call it is passed to ambiguous. A scalar cast gives the identity
    # matrix and says exactly which size it is.
    "matrix2": "matrix2(1)",
    "matrix3": "matrix3(1)",
    "matrix": "matrix(1)",
    "string": '""',
    "dict": "{}",
}

# Implicit widenings, as (source, destination) pairs. Anything not listed needs
# an explicit conversion node.
_WIDENINGS = {
    ("int", "float"),
    ("int", "vector2"), ("int", "vector"), ("int", "vector4"),
    ("float", "vector2"), ("float", "vector"), ("float", "vector4"),
    # Matrices, exactly as vcc accepts them: anything grows into a bigger one,
    # a 4x4 drops to 3x3 (losing translation, which is how `ident()` is used to
    # seed a rotation), and nothing converts *down* to a 2x2. Being stricter
    # than the language here meant refusing to import legal VEX.
    ("matrix2", "matrix3"), ("matrix2", "matrix"),
    ("matrix3", "matrix"), ("matrix", "matrix3"),
}


def zero(vex_type: str) -> str:
    """A sensible empty value for a type, used when a socket is left unwired.

    Empty arrays are spelled with the cast rather than `{}`, because `{}` on its
    own reads as an empty dict and makes any call it is passed to ambiguous.
    """
    if is_array(vex_type):
        return f"{element_type(vex_type)}[](array())"
    return ZERO.get(vex_type, "0")


# The named parts of each vector type. Every component is a float, and a
# vector output socket offers them as pins of its own, so reading `.y` does
# not need a Split Vector node standing in the way.
COMPONENTS = {
    "vector2": ("x", "y"),
    "vector": ("x", "y", "z"),
    "vector4": ("x", "y", "z", "w"),
}


def components_of(vex_type: str) -> tuple[str, ...]:
    return COMPONENTS.get(vex_type, ())


def is_array(vex_type: str) -> bool:
    return vex_type.endswith("[]")


def element_type(vex_type: str) -> str:
    """The member type of an array type; the type itself if it is not one."""
    return vex_type[:-2] if is_array(vex_type) else vex_type


def is_valid(vex_type: str) -> bool:
    return vex_type in ALL_TYPES


def can_connect(source: str, dest: str) -> bool:
    """Whether an output of `source` type may drive an input of `dest` type."""
    if source == dest:
        return True
    if ANY in (source, dest):
        return True  # Unresolved; only reachable if a caller skipped resolution.
    # Arrays never coerce: an int[] into a float[] would need a real conversion,
    # not a cast, and silently doing it elsewhere would be a surprise.
    if is_array(source) or is_array(dest):
        return False
    return (source, dest) in _WIDENINGS


def explain_mismatch(source: str, dest: str) -> str:
    """A message aimed at someone who does not write VEX.

    A bare "type error" teaches nothing. Naming the node that would fix it is
    the difference between a dead end and a next step.
    """
    if can_connect(source, dest):
        return ""
    if is_array(source) and not is_array(dest):
        return (f"This is a list of {element_type(source)} values, but a single "
                f"{dest} is needed. Use a Get Element or Sum node first.")
    if is_array(dest) and not is_array(source):
        return (f"A list of {element_type(dest)} values is needed, but this is a "
                f"single {source}. Use a Make Array node first.")
    if source == "float" and dest == "int":
        return ("A float cannot become a whole number on its own. Add a Round, "
                "Floor or Truncate node to say which way it should go.")
    if source in VECTOR_TYPES and dest in SCALAR_TYPES:
        return (f"A {source} has {VECTOR_TYPES[source]} components. Use Length, "
                "or a Vector to Float node to pick one.")
    if source in VECTOR_TYPES and dest in VECTOR_TYPES:
        return (f"A {source} has {VECTOR_TYPES[source]} components and a {dest} "
                f"has {VECTOR_TYPES[dest]}. Convert it explicitly.")
    if dest == "string" or source == "string":
        return "Text and numbers do not convert on their own. Use a Format node."
    return f"A {source} cannot be used where a {dest} is expected."


def cast_expression(expr: str, source: str, dest: str) -> str:
    """Wrap `expr` so it reads as `dest`, for the widenings VEX allows.

    Mostly a no-op: VEX performs these itself. The exception is a scalar feeding
    a vector, where writing the splat out makes the generated code say what is
    happening instead of leaving the reader to know the rule.
    """
    if source == dest or not can_connect(source, dest):
        return expr
    if source in SCALAR_TYPES and dest in VECTOR_TYPES:
        return f"{dest}({expr})"
    return expr
