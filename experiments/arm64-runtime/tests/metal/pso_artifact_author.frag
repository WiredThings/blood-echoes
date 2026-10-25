#version 450
// Same original VS/FS outputs are reused for both derived pipeline descriptors.
layout(location=0) flat in vec4 velocity;
layout(location=0) out vec4 color;
void main() { color=velocity; }
