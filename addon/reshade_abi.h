/* ReShade 6.8.0 add-on API (version 20) as seen from mingw C, with the MSVC x86 ABI made explicit. Every number here
 * is derived in LAYOUT.md.
 *  - Free exports (ReShadeRegisterAddon, ...) and event callbacks are __cdecl; a C++ `bool` return lives in AL only,
 *    so it is declared `bool` here.
 *  - Interface virtuals are __thiscall: emulated with __fastcall and a dummy second parameter (EDX), so `this` is in
 *    ECX and the real arguments are on the stack; both clean their own stack.
 *  - A member function returning a struct (resource, resource_view, ...) takes a hidden result pointer as its first
 *    stack argument. */
#ifndef DAOC_WORLD_RESHADE_ABI_H
#define DAOC_WORLD_RESHADE_ABI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RS_API_VERSION 20u

/* reshade::addon_event */
enum {
    RS_EV_INIT_EFFECT_RUNTIME = 9,
    RS_EV_DESTROY_EFFECT_RUNTIME = 10,
    RS_EV_CREATE_RESOURCE = 15,
    RS_EV_BIND_RENDER_TARGETS_AND_DEPTH_STENCIL = 41,
    RS_EV_DRAW = 52,
    RS_EV_DRAW_INDEXED = 53,
    RS_EV_PRESENT = 74,
    RS_EV_RESHADE_BEGIN_EFFECTS = 76,
    RS_EV_RESHADE_FINISH_EFFECTS = 77,
};

/* Vtable slots ("VFTable indices"). */
enum {
    RS_SLOT_GET_NATIVE = 0,                 /* api_object: uint64_t get_native() const */
    RS_SLOT_RUNTIME_GET_COMMAND_QUEUE = 8,  /* effect_runtime: command_queue *get_command_queue() */
    RS_SLOT_RUNTIME_RENDER_EFFECTS = 9,     /* effect_runtime: void render_effects(command_list *, resource_view x2) */
    RS_SLOT_QUEUE_GET_IMMEDIATE_LIST = 7,   /* command_queue: command_list *get_immediate_command_list() */
};

/* reshade::api::resource_desc, texture view of the union (MSVC x86: size 48, the union at 8). */
typedef struct {
    uint32_t type; /* resource_type */
    uint32_t pad0;
    uint32_t width;
    uint32_t height;
    uint16_t depth_or_layers;
    uint16_t levels;
    uint32_t format;
    uint16_t samples;
    uint16_t pad1;
    uint32_t pad2;
    uint32_t heap;  /* memory_heap */
    uint32_t usage; /* resource_usage flags */
    uint32_t flags; /* resource_flags */
    uint32_t pad3;
} rs_resource_desc;

_Static_assert(sizeof(rs_resource_desc) == 48, "resource_desc size");
_Static_assert(offsetof(rs_resource_desc, width) == 8, "resource_desc texture.width");
_Static_assert(offsetof(rs_resource_desc, height) == 12, "resource_desc texture.height");
_Static_assert(offsetof(rs_resource_desc, levels) == 18, "resource_desc texture.levels");
_Static_assert(offsetof(rs_resource_desc, format) == 20, "resource_desc texture.format");
_Static_assert(offsetof(rs_resource_desc, samples) == 24, "resource_desc texture.samples");
_Static_assert(offsetof(rs_resource_desc, heap) == 32, "resource_desc heap");
_Static_assert(offsetof(rs_resource_desc, usage) == 36, "resource_desc usage");
_Static_assert(offsetof(rs_resource_desc, flags) == 40, "resource_desc flags");

/* Exports of the ReShade module. */
typedef bool(__cdecl *rs_register_addon_fn)(void *module, uint32_t api_version);
typedef void(__cdecl *rs_unregister_addon_fn)(void *module);
typedef void(__cdecl *rs_register_event_fn)(void *module, uint32_t ev, void *callback);

/* Event callbacks. */
typedef void(__cdecl *rs_effect_runtime_cb)(void *runtime); /* init_effect_runtime, destroy_effect_runtime */
typedef bool(__cdecl *rs_create_resource_cb)(void *device, rs_resource_desc *desc, void *initial_data,
                                             uint32_t initial_state);

#endif
