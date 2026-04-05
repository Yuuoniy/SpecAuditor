# Installation

## Recommended Setup: Docker

The recommended setup path is the provided `Dockerfile`. It prepares the runtime dependencies used by the artifact:

- Ubuntu `22.04`
- Python `3.13.2`, built inside the Docker image
- Python dependencies from `requirements.txt`
- weggli
- tree-sitter-c source files

```bash
docker build -t specauditor-ae .
```

Then start an interactive container:

```bash
docker run -it specauditor-ae
```

## Prepare the codebase and llm endpoint
Inside the container, create the local LLM configuration file:

```bash
cd /workspace/SpecAuditor
cp artifact/config/llm.env.example artifact/config/llm.env
```

Edit `artifact/config/llm.env` and fill in:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

Note: The artifact uses Anthropic’s Claude Sonnet 4 through an OpenAI-compatible endpoint. Please ensure the LLM backend points to a valid compatible service. 

Then prepare the Linux kernel checkout used by the artifact:
Note: Cloning the Linux kernel repository may take over an hour, depending on network speed.
```bash
cd /workspace
git clone --branch v6.17-rc3 https://github.com/torvalds/linux.git linux-v6.17-rc3
```


## Optional setup
The artifact already includes:
- the prebuilt documentation embedding database in `get_docs/kernel_docs_chroma/`
- the packaged retrieval reference CSVs used by the AE workflows

So users do not need to rebuild embeddings or configure `artifact/config/embedding.env` for the packaged AE runs.

If users want to test new hexshas and run stage3 retrieval, also create:

```bash
cp artifact/config/embedding.env.example artifact/config/embedding.env
```

Edit `artifact/config/embedding.env` and fill in:

- `EMBEDDING_API_KEY`
- `EMBEDDING_BASE_URL`
- `EMBEDDING_MODEL`
