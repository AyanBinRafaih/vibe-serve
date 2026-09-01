"""Frontend-serving components for VibeSys applications.

The package intentionally has no eager re-exports. Composition roots import
the specific components they need, and importing a protocol or event model
does not initialize the server runtime.
"""
