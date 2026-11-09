// SPDX-License-Identifier: MIT
// Test double for ABI/lifetime/cancellation checks. Does not perform inference.
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "rkllm.h"
#define API extern "C" __declspec(dllexport)
struct Fake { RKLLMCallback callbacks; int cleared; int abort; };
API RKLLMParam rkllm_createDefaultParam() { RKLLMParam p = {}; return p; }
API int rkllm_init(LLMHandle* handle, RKLLMParam* p, RKLLMCallback* cb) {
    if (!p || !cb || !cb->result_callback || p->is_async || p->ignore_eos_token || p->skip_special_token
        || p->max_context_len != 1024 || p->max_new_tokens != 128) return -10;
    Fake* f = (Fake*)calloc(1, sizeof(Fake));
    f->callbacks = *cb;
    *handle = f;
    return 0;
}
API int rkllm_clear_kv_cache(LLMHandle handle, int keep, int* start, int* end) {
    if (keep || start || end) return -11;
    Fake* f = (Fake*)handle;
    f->cleared = 1;
    __atomic_store_n(&f->abort, 0, __ATOMIC_RELEASE);
    return 0;
}
API int rkllm_run(LLMHandle handle, RKLLMInput* input, RKLLMInferParam* params, void* userdata) {
    Fake* f = (Fake*)handle;
    if (!f->cleared || input->input_type != RKLLM_INPUT_TOKEN || !input->token_input.n_tokens
        || params->keep_history || params->mode != RKLLM_INFER_GENERATE) return -12;
    int token = input->token_input.input_ids[0];
    RKLLMResult r = {};
    if (token == 666) { f->callbacks.result_callback(&r, userdata, RKLLM_RUN_ERROR); return 0; }
    if (token == 777) {
        for (int i = 0; i < 500 && !__atomic_load_n(&f->abort, __ATOMIC_ACQUIRE); ++i) Sleep(10);
        return -13;
    }
    r.text = "\xe4";
    f->callbacks.result_callback(&r, userdata, RKLLM_RUN_NORMAL);
    r.text = "\xbd\xa0\xe5\xa5\xbd";
    f->callbacks.result_callback(&r, userdata, RKLLM_RUN_NORMAL);
    r.text = NULL;
    r.perf.prefill_time_ms = 12.5f;
    r.perf.prefill_tokens = (int)input->token_input.n_tokens;
    r.perf.generate_time_ms = 30.0f;
    r.perf.generate_tokens = 2;
    f->callbacks.result_callback(&r, userdata, RKLLM_RUN_FINISH);
    return 0;
}
API int rkllm_abort(LLMHandle handle) {
    Fake* f = (Fake*)handle;
    __atomic_store_n(&f->abort, 1, __ATOMIC_RELEASE);
    return 0;
}
API int rkllm_destroy(LLMHandle handle) { free(handle); return 0; }
