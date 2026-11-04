# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and Oxlint's TypeScript related rules in your project.

## COPS governance runtime

The collector includes an 18-COP governance runtime in `collector/cops/runtime/`, built on LangGraph. Ten COPs run a bounded LLM investigation and eight run deterministic rules. Architecture, the implementation matrix, evaluation results and open items are in [`docs/cops/RUNTIME.md`](docs/cops/RUNTIME.md).

```
pip install -r collector/requirements-cops-runtime.txt
python3 -m pytest tests/                          # the live-provider test is skipped unless opted in
cd collector && python3 -m cops.runtime.evaluation   # writes docs/cops/evaluation/
```

Every model reply in the tests and the evaluation is a scripted fixture and is labelled simulated. Nothing calls a model provider unless `COPS_LLM_LIVE=1` and `COPS_LLM_DATA_APPROVED=1` are set and credentials are present.
