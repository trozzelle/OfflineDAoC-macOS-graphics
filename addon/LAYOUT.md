# ReShade 6.8.0 ABI facts for the daoc-world add-on

The add-on is C built with mingw-w64 (`i686-w64-mingw32-gcc`); ReShade is C++ built with MSVC for x86. Everything
`reshade_abi.h` hard-codes comes from this file. Source: the ReShade v6.8.0 `include/` folder
(https://github.com/crosire/reshade/tree/v6.8.0/include), add-on API version `RESHADE_API_VERSION 20`
(`reshade.hpp:13`).

## How the numbers were made

clang lays the headers out for the MSVC x86 target. The host has no MSVC headers, so three stand-in headers supply
what `reshade_*.hpp` include (`<cstdint>`, `<cstddef>`, `<cfloat>`):

```text
stub/cstdint  typedefs uint8_t..uint64_t, int8_t..int64_t, uintptr_t (unsigned int) in :: and std::,
              plus UINT64_MAX, UINT32_MAX, UINT16_MAX, INT32_MAX
stub/cstddef  typedef unsigned int size_t; typedef int ptrdiff_t; (in :: and std::)
stub/cfloat   #define FLT_MAX 3.402823466e+38F
```

```bash
FLAGS="-target i686-pc-windows-msvc -std=c++17 -fms-extensions -fms-compatibility -nostdinc -isystem stub \
       -I/tmp/reshade680/include"
# Event numbers: an `extern "C" const unsigned vals[] = { (unsigned)addon_event::X, ... };` read back from the .s
clang++ $FLAGS -S -o ev.s ev.cpp
# Vtables: a function that calls one virtual of each interface (r->get_command_queue(),
# q->get_immediate_command_list(), d->get_api(), s->get_current_back_buffer(), c->draw(0,0,0,0))
clang++ $FLAGS -S -o /dev/null -Xclang -fdump-vtable-layouts vt.cpp
# Records: a global `resource_desc` plus sizeof() values read back from the .s
clang++ $FLAGS -S -o rec.s -Xclang -fdump-record-layouts rec.cpp
```

Vtable slots below are the **"VFTable indices for"** section: the index into the vtable the object points at. The
**"VFTable for"** listing counts the RTTI entry (which lives at `vtable[-1]`) as row 0, so its rows are one higher.

## Calling conventions (MSVC x86)

- Free exports (`ReShadeRegisterAddon`, `ReShadeRegisterEventForAddon`, ...) and event callbacks: `__cdecl`.
  `bool` is returned in `AL` only; the upper bytes of `EAX` are undefined, so C declares these returns `bool`.
- `AddonInit` / `AddonUninit`: ReShade calls them through `bool(*)(HMODULE addon_module, HMODULE reshade_module)` /
  `void(*)(HMODULE, HMODULE)` (`source/addon_manager.cpp:296`, `:355` at v6.8.0), i.e. `__cdecl`. ReShade reads the
  exports `NAME` and `DESCRIPTION` as `const char *const *` (`addon_manager.cpp:483`, `:487`).
- Interface virtuals are `__thiscall` (`this` in `ECX`, callee cleans the stack). mingw C emulates it with
  `__fastcall` and a dummy second parameter (`EDX`); every real argument then goes on the stack.
- A member function returning a struct (`resource`, `resource_view`, `effect_uniform_variable`, ... — all 8 bytes)
  returns through a hidden result pointer passed as the first stack argument (after `this`); the callee returns
  that pointer in `EAX`. A `uint64_t` return (`get_native`) comes back in `EDX:EAX`.
- References (`resource_desc &`) are pointers. 8-byte handle structs passed by value (`resource_view`, `resource`)
  take 8 bytes of stack, the same as a `uint64_t` argument in C.

## Event numbers (`reshade::addon_event`, `reshade_events.hpp`)

Clang's values agree with counting the enum from its anchors `destroy_swapchain = 8` and
`update_buffer_region = 24`.

| event | value | callback (all `__cdecl`) |
| --- | --- | --- |
| `destroy_swapchain` (anchor) | 8 | |
| `init_effect_runtime` | 9 | `void (effect_runtime *runtime)` |
| `destroy_effect_runtime` | 10 | `void (effect_runtime *runtime)` |
| `create_resource` | 15 | `bool (device *device, resource_desc &desc, subresource_data *initial_data, resource_usage initial_state)` — modify `desc` and return true to replace the description, else return false |
| `update_buffer_region` (anchor) | 24 | |
| `bind_render_targets_and_depth_stencil` | 41 | `void (command_list *cmd_list, uint32_t count, const resource_view *rtvs, resource_view dsv)` |
| `draw` | 52 | `bool (command_list *cmd_list, uint32_t vertex_count, uint32_t instance_count, uint32_t first_vertex, uint32_t first_instance)` — true skips the draw |
| `draw_indexed` | 53 | `bool (command_list *cmd_list, uint32_t index_count, uint32_t instance_count, uint32_t first_index, int32_t vertex_offset, uint32_t first_instance)` — true skips the draw |
| `present` | 74 | `void (command_queue *queue, swapchain *swapchain, const rect *source_rect, const rect *dest_rect, uint32_t dirty_rect_count, const rect *dirty_rects)` |
| `reshade_present` | 75 | `void (effect_runtime *runtime)` |
| `reshade_begin_effects` | 76 | `void (effect_runtime *runtime, command_list *cmd_list, resource_view rtv, resource_view rtv_srgb)` |
| `reshade_finish_effects` | 77 | `void (effect_runtime *runtime, command_list *cmd_list, resource_view rtv, resource_view rtv_srgb)` |

Signatures: `RESHADE_DEFINE_ADDON_EVENT_TRAITS` lines `reshade_events.hpp:1846-1947`.

## Vtable slots

`api_object` (base of every interface):

| slot | method |
| --- | --- |
| 0 | `uint64_t get_native() const` — for d3d9, `device` → `IDirect3DDevice9 *` |
| 1 | `void get_private_data(const uint8_t *guid, uint64_t *data) const` |
| 2 | `void set_private_data(const uint8_t *guid, const uint64_t data)` |

`device_object : api_object`: slot 3 `device *get_device()`.

`device : api_object` (selected): 3 `device_api get_api() const`, 4 `check_capability`, 5 `check_format_support`,
8 `bool create_resource(const resource_desc &, const subresource_data *, resource_usage, resource *, void **)`,
9 `void destroy_resource(resource)`, 10 `resource_desc get_resource_desc(resource) const`,
13 `resource get_resource_from_view(resource_view) const`. `device::get_native` is slot 0.

`effect_runtime : device_object` (slots 4-9 and the effects switch):

| slot | method |
| --- | --- |
| 4 | `void *get_hwnd() const` |
| 5 | `resource get_back_buffer(uint32_t index)` (struct return: hidden result pointer) |
| 6 | `uint32_t get_back_buffer_count() const` |
| 7 | `uint32_t get_current_back_buffer_index() const` |
| 8 | `command_queue *get_command_queue()` |
| 9 | `void render_effects(command_list *cmd_list, resource_view rtv, resource_view rtv_srgb)` |
| 20 | `effect_uniform_variable find_uniform_variable(const char *, const char *) const` |
| 33 | `void set_uniform_value_float(effect_uniform_variable, const float *, size_t, size_t)` |
| 59 | `void render_technique(effect_technique, command_list *, resource_view, resource_view)` |
| 60 | `bool get_effects_state() const` |
| 61 | `void set_effects_state(bool enabled)` |

`swapchain : device_object` (the `present` event's object): 4 `get_hwnd`, 5 `resource get_back_buffer(uint32_t)`,
6 `get_back_buffer_count`, 7 `uint32_t get_current_back_buffer_index() const`, 8 `check_color_space_support`,
9 `get_color_space`. In 6.8.0 `effect_runtime` derives from `device_object`, not from `swapchain`; slots 4-7 happen to
match.

`command_queue : device_object`:

| slot | method |
| --- | --- |
| 4 | `command_queue_type get_type() const` |
| 5 | `void wait_idle() const` |
| 6 | `void flush_immediate_command_list() const` |
| 7 | `command_list *get_immediate_command_list()` |

`command_list : device_object` (selected): 7 `bind_render_targets_and_depth_stencil(uint32_t, const resource_view *,
resource_view)`, 10 `bind_viewports(uint32_t first, uint32_t count, const viewport *)`, 18 `draw`, 19 `draw_indexed`.

## Records (MSVC x86)

`resource_desc`: size 48, align 8.

| offset | field | notes |
| --- | --- | --- |
| 0 | `resource_type type` (uint32) | 0 unknown, 1 buffer, 2 texture_1d, 3 texture_2d, 4 texture_3d, 5 surface |
| 4 | padding | the union holds a `uint64_t`, so it starts at 8 |
| 8 | `texture.width` (uint32) | union with `buffer.size` (uint64 at 8) |
| 12 | `texture.height` (uint32) | |
| 16 | `texture.depth_or_layers` (uint16) | union with `buffer.structured.stride` (uint32 at 16) |
| 18 | `texture.levels` (uint16) | |
| 20 | `texture.format` (uint32 enum) | |
| 24 | `texture.samples` (uint16) | union ends at 32 |
| 32 | `memory_heap heap` (uint32) | |
| 36 | `resource_usage usage` (uint32 flags) | render_target 0x4, depth_stencil 0x30 (read 0x20, write 0x10), shader_resource 0xC0, copy_dest 0x400, copy_source 0x800 |
| 40 | `resource_flags flags` (uint32) | |
| 44 | padding | |

Other records: `resource`, `resource_view` = one `uint64_t handle` (8, align 8); `subresource_data` = `void *data`,
`uint32_t row_pitch`, `uint32_t slice_pitch` (12); `rect` = `int32_t left, top, right, bottom` (16); `viewport` =
six floats `x, y, width, height, min_depth, max_depth` (24). Enums (`resource_usage`, `format`, `resource_type`,
`memory_heap`, `resource_flags`) are 4 bytes.

## How ReShade's d3d9 layer fills `create_resource` (source/d3d9, v6.8.0)

- `IDirect3DDevice9::CreateTexture` (`d3d9_device.cpp:422`): `type = texture_2d`, `texture.width/height` from the
  call, `usage` gains `render_target` for `D3DUSAGE_RENDERTARGET`; `initial_state = general`. Textures whose format
  ReShade cannot convert skip the event.
- `CreateDepthStencilSurface` (`d3d9_device.cpp:924`): `type = surface`, `usage` has `depth_stencil`;
  `initial_state = depth_stencil`.
- After a callback returns true, ReShade converts the modified `desc` back to the D3D9 call's arguments, so
  changing `texture.width/height` changes the size the game's object is created with.
