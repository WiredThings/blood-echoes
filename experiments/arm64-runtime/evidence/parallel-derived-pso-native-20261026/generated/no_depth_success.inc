// Exact frozen added block; authored invocation observation only.
if (it->second && !key.motion_vectors) {
            GraphicsSerialization serialized;
            const auto attributes = pipeline.VertexAttributes();
            serialized.vertex_attributes.assign(attributes.begin(), attributes.end());
            VideoCore::RegisterPipelineData(key, std::hash<VideoCore::GraphicsPipelineKey>{}(key), serialized);
        }
