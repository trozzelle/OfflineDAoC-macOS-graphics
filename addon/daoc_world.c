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
 * Effects before the UI (effects_before_ui = 1): the first draw of a frame to the back buffer that is orthographic
 * (projection _34 == 0 and _44 == 1, or XYZRHW vertices), after at least one perspective back-buffer draw, is the
 * 3D/UI boundary (world_core.h). Right before that draw the add-on calls the runtime's render_effects on the back
 * buffer, between a capture and an apply of a D3DSBT_ALL state block. render_effects marks the frame's effects done,
 * so ReShade skips its own pass at Present that frame (runtime.cpp at v6.8.0, `_effects_rendered_this_frame`);
 * character select draws 3D models, so it fires the boundary too; frames with no perspective back-buffer draw (login,
 * loading) still get the effects at Present. After the boundary, and when
 * the frame draws offscreen, a draw costs a flag test.
 *
 * Reads daoc-world.ini beside this file once, at load (defaults: effects_before_ui 0, reflection_scale 1), and
 * writes daoc-world.log there. The draw, bind and present events are registered only with effects_before_ui = 1.
 */
#include <windows.h>
#include <d3d9.h>
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
__declspec(dllexport) const char *DESCRIPTION =
    "DAoC world effects: ReShade effects before the UI, a larger water reflection render target";

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

/* Effects before the UI. In ReShade's d3d9 layer a surface's resource handle and its render target view handle are
 * the surface pointer; bit 0 of a view handle selects sRGB writes (d3d9_impl_device.cpp create_resource_view). */
static rs_handle back_buffer;   /* the runtime's current back buffer, read at init_effect_runtime */
static int rt0_is_back_buffer;  /* render target 0 is the back buffer (bind_render_targets_and_depth_stencil) */
static struct dw_frame frame;
static IDirect3DStateBlock9 *state_block;
static unsigned presents;      /* since the runtime came up, for the log */
static int logged_fire, logged_no_transform, logged_no_state_block;

/* ------------------------------------------------------------------ log */

static void log_line(const char *fmt, ...)
{
    char buf[1024 + 2]; /* wvsprintfA writes at most 1024 chars, plus CR LF */
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

static void release_state_block(void)
{
    if (state_block)
        IDirect3DStateBlock9_Release(state_block);
    state_block = NULL;
}

static void __cdecl on_init_effect_runtime(void *rt)
{
    uint32_t index;
    runtime = rt;
    if (cfg.effects_before_ui) {
        index = RS_VCALL(rt, rs_get_current_back_buffer_index_fn, RS_SLOT_RUNTIME_GET_CURRENT_BACK_BUFFER_INDEX);
        RS_VCALL(rt, rs_get_back_buffer_fn, RS_SLOT_RUNTIME_GET_BACK_BUFFER, &back_buffer, index);
        rt0_is_back_buffer = 1; /* device creation and Reset leave render target 0 on the back buffer */
        dw_frame_reset(&frame);
        presents = 0;
        logged_fire = 0;
    }
    log_line("effect runtime %p up, back buffer %p", rt, (void *)(uintptr_t)back_buffer);
}

/* ReShade destroys the runtime before the device's Reset (d3d9_device.cpp Reset → on_reset), which fails while a
 * state block is alive. */
static void __cdecl on_destroy_effect_runtime(void *rt)
{
    if (runtime == rt) {
        runtime = NULL;
        back_buffer = 0;
        release_state_block();
    }
    log_line("effect runtime %p down", rt);
}

/* ------------------------------------------------------- effects before the UI */

static void __cdecl on_bind_render_targets(void *cmd_list, uint32_t count, const rs_handle *rtvs, rs_handle dsv)
{
    rt0_is_back_buffer = count > 0 && back_buffer != 0 && rtvs[0] == back_buffer;
}

/* Runs the effects on the back buffer now, from inside the boundary draw's callback (before the draw executes). */
static void render_before_ui(IDirect3DDevice9 *dev)
{
    void *queue, *list;
    if (!state_block) {
        if (FAILED(IDirect3DDevice9_CreateStateBlock(dev, D3DSBT_ALL, &state_block))) {
            state_block = NULL;
            if (!logged_no_state_block) {
                logged_no_state_block = 1;
                log_line("error: CreateStateBlock(D3DSBT_ALL) failed: effects stay at Present");
            }
            return;
        }
    }
    IDirect3DStateBlock9_Capture(state_block);
    queue = RS_VCALL(runtime, rs_get_object_fn, RS_SLOT_RUNTIME_GET_COMMAND_QUEUE);
    list = RS_VCALL(queue, rs_get_object_fn, RS_SLOT_QUEUE_GET_IMMEDIATE_LIST);
    /* rtv_srgb is the same surface with the sRGB bit, as ReShade's own back buffer views at Present. */
    RS_VCALL(runtime, rs_render_effects_fn, RS_SLOT_RUNTIME_RENDER_EFFECTS, list, back_buffer, back_buffer | 1u);
    IDirect3DStateBlock9_Apply(state_block);
    if (!logged_fire) {
        logged_fire = 1;
        log_line("effects before the UI: first boundary after %u present(s)", presents);
    }
}

static void at_draw(void *cmd_list)
{
    IDirect3DDevice9 *dev;
    D3DMATRIX proj;
    DWORD fvf;
    /* Offscreen draws (shadow, reflection) never fire or count as the world: skip the device queries. */
    if (frame.fired || !runtime || !rt0_is_back_buffer)
        return;
    dev = (IDirect3DDevice9 *)(uintptr_t)RS_VCALL(cmd_list, rs_get_native_fn, RS_SLOT_GET_NATIVE);
    if (FAILED(IDirect3DDevice9_GetFVF(dev, &fvf)))
        fvf = 0;
    if (FAILED(IDirect3DDevice9_GetTransform(dev, D3DTS_PROJECTION, &proj))) {
        if (!logged_no_transform) {
            logged_no_transform = 1;
            log_line("GetTransform(D3DTS_PROJECTION) failed: such draws count as perspective");
        }
        proj._34 = 1.0f;
        proj._44 = 0.0f;
    }
    if (dw_should_fire(&frame, 1, dw_ortho_or_rhw(proj._34, proj._44, fvf)))
        render_before_ui(dev);
}

static bool __cdecl on_draw(void *cmd_list, uint32_t vertex_count, uint32_t instance_count, uint32_t first_vertex,
                            uint32_t first_instance)
{
    at_draw(cmd_list);
    return false;
}

static bool __cdecl on_draw_indexed(void *cmd_list, uint32_t index_count, uint32_t instance_count,
                                    uint32_t first_index, int32_t vertex_offset, uint32_t first_instance)
{
    at_draw(cmd_list);
    return false;
}

/* ReShade invokes this before its own effect pass at Present (d3d9_swapchain.cpp on_present). */
static void __cdecl on_present(void *queue, void *swapchain, const void *source_rect, const void *dest_rect,
                               uint32_t dirty_rect_count, const void *dirty_rects)
{
    dw_frame_reset(&frame);
    presents++;
}

/* ------------------------------------------------------- entry points */

/* Typed here so a signature that drifts from reshade_abi.h fails to compile. */
static const rs_effect_runtime_cb cb_init_runtime = on_init_effect_runtime;
static const rs_effect_runtime_cb cb_destroy_runtime = on_destroy_effect_runtime;
static const rs_create_resource_cb cb_create_resource = on_create_resource;
static const rs_bind_render_targets_cb cb_bind_render_targets = on_bind_render_targets;
static const rs_draw_cb cb_draw = on_draw;
static const rs_draw_indexed_cb cb_draw_indexed = on_draw_indexed;
static const rs_present_cb cb_present = on_present;

/* ReShade calls this (as bool(*)(HMODULE, HMODULE), cdecl) after loading the add-on; false unloads it. */
static void close_log(void)
{
    if (log_file != INVALID_HANDLE_VALUE)
        CloseHandle(log_file);
    log_file = INVALID_HANDLE_VALUE;
}

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
        close_log();
        return false;
    }
    if (!reg(addon_module, RS_API_VERSION)) {
        log_line("error: ReShadeRegisterAddon(api %u) refused", RS_API_VERSION);
        close_log();
        return false;
    }
    ev(addon_module, RS_EV_INIT_EFFECT_RUNTIME, (void *)cb_init_runtime);
    ev(addon_module, RS_EV_DESTROY_EFFECT_RUNTIME, (void *)cb_destroy_runtime);
    ev(addon_module, RS_EV_CREATE_RESOURCE, (void *)cb_create_resource);
    if (cfg.effects_before_ui) {
        ev(addon_module, RS_EV_BIND_RENDER_TARGETS_AND_DEPTH_STENCIL, (void *)cb_bind_render_targets);
        ev(addon_module, RS_EV_DRAW, (void *)cb_draw);
        ev(addon_module, RS_EV_DRAW_INDEXED, (void *)cb_draw_indexed);
        ev(addon_module, RS_EV_PRESENT, (void *)cb_present);
    }
    log_line("registered (api %u): init/destroy_effect_runtime, create_resource%s", RS_API_VERSION,
             cfg.effects_before_ui ? ", bind_render_targets_and_depth_stencil, draw, draw_indexed, present" : "");
    return true;
}

/* ReShade calls this before unloading the add-on: unregistering drops its event callbacks. */
__declspec(dllexport) void AddonUninit(HMODULE addon_module, HMODULE reshade_module)
{
    rs_unregister_addon_fn unreg = (rs_unregister_addon_fn)GetProcAddress(reshade_module, "ReShadeUnregisterAddon");
    if (unreg)
        unreg(addon_module);
    log_line("unloaded");
    close_log();
}
