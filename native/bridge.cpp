// SPDX-License-Identifier: MIT
// Independent stable C ABI around the official RKLLM 1.3.0 SDK.
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rkllm.h"

#ifdef _WIN32
#include <windows.h>
#define EXPORT extern "C" __declspec(dllexport)
static void* open_library(const char* p) {
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, p, -1, NULL, 0);
    if (!count) return NULL;
    wchar_t* path = (wchar_t*)malloc(count * sizeof(wchar_t));
    if (!path) return NULL;
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, p, -1, path, count);
    void* library = (void*)LoadLibraryW(path);
    free(path);
    return library;
}
static void* symbol(void* h, const char* n) { return (void*)GetProcAddress((HMODULE)h, n); }
static void close_library(void* h) { FreeLibrary((HMODULE)h); }
static const char* loader_error() { return "Cannot load runtime or one of its dependencies"; }
#else
#include <dlfcn.h>
#define EXPORT extern "C" __attribute__((visibility("default")))
static void* open_library(const char* p) { return dlopen(p, RTLD_NOW | RTLD_LOCAL); }
static void* symbol(void* h, const char* n) { return dlsym(h, n); }
static void close_library(void* h) { dlclose(h); }
static const char* loader_error() { const char* p = dlerror(); return p ? p : "Cannot load runtime"; }
#endif

struct LAEvent {
    int32_t state;
    int32_t token_id;
    const char* text;
    size_t text_len;
    float prefill_ms;
    int32_t prefill_tokens;
    float generate_ms;
    int32_t generate_tokens;
};
typedef void (*LASink)(void*, const LAEvent*);

struct Context {
    void* library;
    LLMHandle handle;
    char* model_path;
    RKLLMCallback callbacks;
    LASink sink;
    void* userdata;
    int active;
    RKLLMParam (*defaults)();
    int (*init)(LLMHandle*, RKLLMParam*, RKLLMCallback*);
    int (*run)(LLMHandle, RKLLMInput*, RKLLMInferParam*, void*);
    int (*abort)(LLMHandle);
    int (*destroy)(LLMHandle);
    int (*clear)(LLMHandle, int, int*, int*);
};

static int receive(RKLLMResult* result, void* userdata, LLMCallState state) {
    Context* c = (Context*)userdata;
    if (!c || !c->sink) return 0;
    LAEvent event = {};
    event.state = (int32_t)state;
    if (result) {
        event.token_id = result->token_id;
        event.text = result->text;
        event.text_len = result->text ? strlen(result->text) : 0;
        if (state == RKLLM_RUN_FINISH) {
            event.prefill_ms = result->perf.prefill_time_ms;
            event.prefill_tokens = result->perf.prefill_tokens;
            event.generate_ms = result->perf.generate_time_ms;
            event.generate_tokens = result->perf.generate_tokens;
        }
    }
    c->sink(c->userdata, &event);
    return 0;
}

static void fail(char* output, size_t capacity, const char* text) {
    if (output && capacity) snprintf(output, capacity, "%s", text ? text : "Runtime error");
}

EXPORT int32_t la_abi() { return 1; }

EXPORT void* la_open(const char* runtime, const char* model, int32_t context,
                     int32_t maximum, float temperature, char* error, size_t capacity) {
    if (!runtime || !model || context < 128 || maximum <= 0 || maximum >= context) {
        fail(error, capacity, "Invalid native configuration"); return NULL;
    }
    Context* c = (Context*)calloc(1, sizeof(Context));
    if (!c) { fail(error, capacity, "Allocation failed"); return NULL; }
    c->library = open_library(runtime);
    if (!c->library) { fail(error, capacity, loader_error()); free(c); return NULL; }
#define LOAD(member, api) c->member = (decltype(c->member))symbol(c->library, api)
    LOAD(defaults, "rkllm_createDefaultParam");
    LOAD(init, "rkllm_init"); LOAD(run, "rkllm_run");
    LOAD(abort, "rkllm_abort"); LOAD(destroy, "rkllm_destroy");
    LOAD(clear, "rkllm_clear_kv_cache");
#undef LOAD
    if (!c->defaults || !c->init || !c->run || !c->abort || !c->destroy || !c->clear) {
        fail(error, capacity, "Required RKLLM 1.3.0 symbols are missing");
        close_library(c->library); free(c); return NULL;
    }
    c->model_path = (char*)malloc(strlen(model) + 1);
    if (!c->model_path) {
        fail(error, capacity, "Allocation failed"); close_library(c->library); free(c); return NULL;
    }
    strcpy(c->model_path, model);
    RKLLMParam parameters = c->defaults();
    parameters.model_path = c->model_path;
    parameters.max_context_len = context;
    parameters.max_new_tokens = maximum;
    parameters.temperature = temperature;
    parameters.top_k = 40;
    parameters.top_p = 0.9f;
    parameters.repeat_penalty = 1.1f;
    parameters.is_async = false;
    parameters.skip_special_token = false; // Preserve <think> boundaries.
    parameters.ignore_eos_token = false;
    parameters.extend_param.n_batch = 1;
    c->callbacks.result_callback = receive;
    c->callbacks.result_userdata = c;
    int status = c->init(&c->handle, &parameters, &c->callbacks);
    if (status != 0 || !c->handle) {
        if (error && capacity) snprintf(error, capacity, "rkllm_init failed: %d", status);
        if (c->handle) c->destroy(c->handle);
        free(c->model_path); close_library(c->library); free(c); return NULL;
    }
    return c;
}

EXPORT int32_t la_run(void* opaque, const int32_t* tokens, size_t count,
                     LASink sink, void* userdata) {
    Context* c = (Context*)opaque;
    if (!c || !tokens || !count || !sink) return -1;
    int expected = 0;
    if (!__atomic_compare_exchange_n(&c->active, &expected, 1, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return -2;
    c->sink = sink; c->userdata = userdata;
    int status = c->clear(c->handle, 0, NULL, NULL);
    if (status == 0) {
        RKLLMInput input = {};
        input.role = "user";
        input.enable_thinking = true;
        input.input_type = RKLLM_INPUT_TOKEN;
        input.token_input.input_ids = const_cast<int32_t*>(tokens);
        input.token_input.n_tokens = count;
        RKLLMInferParam inference = {};
        inference.mode = RKLLM_INFER_GENERATE;
        inference.keep_history = 0; // Every request includes its own full prompt.
        status = c->run(c->handle, &input, &inference, c);
    }
    c->sink = NULL; c->userdata = NULL;
    __atomic_store_n(&c->active, 0, __ATOMIC_RELEASE);
    return status;
}

EXPORT int32_t la_abort(void* opaque) {
    Context* c = (Context*)opaque;
    return c && __atomic_load_n(&c->active, __ATOMIC_ACQUIRE) ? c->abort(c->handle) : 0;
}

EXPORT int32_t la_close(void* opaque) {
    Context* c = (Context*)opaque;
    if (!c) return 0;
    if (__atomic_load_n(&c->active, __ATOMIC_ACQUIRE)) return -2;
    int status = c->destroy(c->handle);
    if (status != 0) return status;
    free(c->model_path); close_library(c->library); free(c);
    return 0;
}
