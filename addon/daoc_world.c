/* daoc-world: a ReShade add-on for the DAoC client (32-bit, loaded by ReShade from daoc-world.addon32).
 *
 * ReShade loads every *.addon32 in its add-on folder (its own folder unless [ADDON] AddonPath says otherwise) and
 * calls AddonInit, which registers this module with the add-on API (version 20; the hand-written MSVC x86 ABI is in
 * reshade_abi.h, its numbers in LAYOUT.md) and its event callbacks.
 *
 * Water reflection (reflection_scale): the game renders the scene's reflection into a 512x512 render target created
 * from game.dll call site 0x56a930. create_resource sees every texture and depth-stencil the game makes; when the
 * call stack holds that call site, a 512x512 render target or depth-stencil is created reflection_scale times larger.
 * Other 512x512 render targets and depth-stencils are logged with their game.dll return addresses, so a depth-stencil
 * the reflection pass makes elsewhere shows up in the log.
 *
 * Reads daoc-world.ini beside this file once, at load (defaults: effects_before_ui 0, reflection_scale 1), and
 * writes daoc-world.log there.
 */
#include <windows.h>
#include <stdarg.h>

#include "reshade_abi.h"
#include "world_core.h"

#ifndef SOURCE_HASH
#define SOURCE_HASH "unknown"
#endif

/* Recognised by daoc-gfx (and by `strings`) as this add-on and its source. */
__attribute__((used)) static const char marker[] = "daoc-gfx daoc-world add-on, source " SOURCE_HASH;

/* ReShade reads these for its add-on list (as `const char *const *`). */
__declspec(dllexport) const char *NAME = "daoc-world";
__declspec(dllexport) const char *DESCRIPTION = "DAoC world effects: a larger water reflection render target";

/* game.dll's image: words on the stack in this range are its return addresses. */
#define GAME_LO 0x401000u
#define GAME_HI 0x79bad2u
/* How far up the stack to look (from the callback, through ReShade's d3d9 layer, to the game's call), and how many
 * game.dll words to keep. */
#define SCAN_WORDS 1024
#define KEEP_WORDS 48

static HMODULE self_module;
static HANDLE log_file = INVALID_HANDLE_VALUE;
static dw_config cfg;
static void *runtime;

/* ------------------------------------------------------------------ log */

static void log_line(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    DWORD n, written;
    if (log_file == INVALID_HANDLE_VALUE)
        return;
    va_start(ap, fmt);
    n = (DWORD)wvsprintfA(buf, fmt, ap);
    va_end(ap);
    if (n > sizeof(buf) - 3)
        n = sizeof(buf) - 3;
    buf[n++] = '\r';
    buf[n++] = '\n';
    WriteFile(log_file, buf, n, &written, NULL);
}

/* `name` in this add-on's folder, in `path`; FALSE when it does not fit. */
static BOOL beside(WCHAR path[MAX_PATH], const WCHAR *name)
{
    DWORD n = GetModuleFileNameW(self_module, path, MAX_PATH);
    WCHAR *slash;
    if (!n || n >= MAX_PATH)
        return FALSE;
    slash = wcsrchr(path, L'\\');
    if (!slash || (size_t)(slash - path) + 1 + lstrlenW(name) >= MAX_PATH)
        return FALSE;
    lstrcpyW(slash + 1, name);
    return TRUE;
}

static void open_log(void)
{
    WCHAR path[MAX_PATH];
    if (beside(path, L"daoc-world.log"))
        log_file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
}

static void read_config(void)
{
    WCHAR path[MAX_PATH];
    char text[4096], warn[160];
    DWORD got = 0;
    HANDLE f;
    int rejected;
    cfg = dw_default();
    if (!beside(path, L"daoc-world.ini"))
        return;
    f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) {
        log_line("no daoc-world.ini: defaults");
    } else {
        ReadFile(f, text, sizeof(text), &got, NULL);
        CloseHandle(f);
        rejected = dw_parse(text, got, &cfg, warn, sizeof(warn));
        if (rejected)
            log_line("daoc-world.ini: %d line(s) rejected, first: %s", rejected, warn);
    }
    log_line("settings: effects_before_ui %d, reflection_scale %d", cfg.effects_before_ui, cfg.reflection_scale);
}

/* ------------------------------------------------------- reflection target */

/* game.dll return addresses on this thread's stack, nearest first (call-site evidence). */
static int stack_scan(uint32_t *out, int max)
{
    const uint32_t *sp = (const uint32_t *)__builtin_frame_address(0);
    const uint32_t *base = (const uint32_t *)((NT_TIB *)NtCurrentTeb())->StackBase;
    int n = 0;
    for (int i = 0; i < SCAN_WORDS && sp + i < base && n < max; i++)
        if (sp[i] >= GAME_LO && sp[i] < GAME_HI)
            out[n++] = sp[i];
    return n;
}

static bool __cdecl on_create_resource(void *device, rs_resource_desc *desc, void *initial_data,
                                       uint32_t initial_state)
{
    uint32_t st[KEEP_WORDS], size;
    int n;
    char kind = dw_reflection_kind(desc->type, desc->usage, desc->width, desc->height);
    const char *what = kind == 'R' ? "render target" : "depth-stencil";
    if (!kind)
        return false;
    n = stack_scan(st, KEEP_WORDS);
    if (!dw_stack_is_water_rt(st, n)) {
        log_line("512x512 %s (format %u), not the water call site: game.dll 0x%x 0x%x 0x%x", what,
                 (unsigned)desc->format, n > 0 ? st[0] : 0u, n > 1 ? st[1] : 0u, n > 2 ? st[2] : 0u);
        return false;
    }
    size = dw_scaled_size(desc->width, cfg.reflection_scale);
    if (size == desc->width) {
        log_line("reflection %s 512x512 (format %u), kept", what, (unsigned)desc->format);
        return false;
    }
    desc->width = desc->height = size;
    log_line("reflection %s 512x512 -> %ux%u (format %u)", what, size, size, (unsigned)desc->format);
    return true;
}

/* ------------------------------------------------------- effect runtime */

static void __cdecl on_init_effect_runtime(void *rt)
{
    runtime = rt;
    log_line("effect runtime %p up", rt);
}

static void __cdecl on_destroy_effect_runtime(void *rt)
{
    if (runtime == rt)
        runtime = NULL;
    log_line("effect runtime %p down", rt);
}

/* ------------------------------------------------------- entry points */

/* Typed here so a signature that drifts from reshade_abi.h fails to compile. */
static const rs_effect_runtime_cb cb_init_runtime = on_init_effect_runtime;
static const rs_effect_runtime_cb cb_destroy_runtime = on_destroy_effect_runtime;
static const rs_create_resource_cb cb_create_resource = on_create_resource;

/* ReShade calls this (as bool(*)(HMODULE, HMODULE), cdecl) after loading the add-on; false unloads it. */
__declspec(dllexport) bool AddonInit(HMODULE addon_module, HMODULE reshade_module)
{
    rs_register_addon_fn reg;
    rs_register_event_fn ev;
    self_module = addon_module;
    open_log();
    log_line("%s", marker);
    read_config();
    reg = (rs_register_addon_fn)GetProcAddress(reshade_module, "ReShadeRegisterAddon");
    ev = (rs_register_event_fn)GetProcAddress(reshade_module, "ReShadeRegisterEventForAddon");
    if (!reg || !ev) {
        log_line("error: ReShade exports missing (register %p, event %p)", (void *)reg, (void *)ev);
        return false;
    }
    if (!reg(addon_module, RS_API_VERSION)) {
        log_line("error: ReShadeRegisterAddon(api %u) refused", RS_API_VERSION);
        return false;
    }
    ev(addon_module, RS_EV_INIT_EFFECT_RUNTIME, (void *)cb_init_runtime);
    ev(addon_module, RS_EV_DESTROY_EFFECT_RUNTIME, (void *)cb_destroy_runtime);
    ev(addon_module, RS_EV_CREATE_RESOURCE, (void *)cb_create_resource);
    log_line("registered (api %u): init/destroy_effect_runtime, create_resource", RS_API_VERSION);
    return true;
}

/* ReShade calls this before unloading the add-on: unregistering drops its event callbacks. */
__declspec(dllexport) void AddonUninit(HMODULE addon_module, HMODULE reshade_module)
{
    rs_unregister_addon_fn unreg = (rs_unregister_addon_fn)GetProcAddress(reshade_module, "ReShadeUnregisterAddon");
    if (unreg)
        unreg(addon_module);
    log_line("unloaded");
    if (log_file != INVALID_HANDLE_VALUE)
        CloseHandle(log_file);
    log_file = INVALID_HANDLE_VALUE;
}
