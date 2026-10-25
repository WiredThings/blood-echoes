#version 450
// Authored regression input. No Bloodborne binary or shader bytes.
layout(location=2) in vec2 position;
layout(location=7) in vec4 original_velocity;
layout(location=0) flat out vec4 velocity;
void main() {
    gl_Position=vec4(position,0.5,1.0);
    velocity=original_velocity;
}
