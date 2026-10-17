/* daoc-world add-on: the rules, in plain C with no Windows headers, so test_world.c can build them for the host.
 * daoc_world.c is the ReShade glue that feeds them. */
#ifndef DAOC_WORLD_CORE_H
#define DAOC_WORLD_CORE_H

#include <stddef.h>
#include <stdint.h>

/* The water reflection render target is created from game.dll call site 0x56a930 (the return address on the stack),
 * which lies in [LO, HI). The shadow builder's sites (0x574853, 0x5730de, 0x573062) do not. */
#define DW_WATER_CALL_LO 0x56a900u
#define DW_WATER_CALL_HI 0x56a960u
/* The game's reflection target size, and the largest size the add-on makes it. */
#define DW_REFLECTION_NATIVE 512u
#define DW_REFLECTION_MAX 2048u

/* reshade::api values the rules read (LAYOUT.md). */
#define DW_TYPE_TEXTURE_2D 3u
#define DW_TYPE_SURFACE 5u
#define DW_USAGE_RENDER_TARGET 0x4u
#define DW_USAGE_DEPTH_STENCIL 0x30u

/* D3D9 FVF bits the boundary rule reads (d3d9types.h). */
#define DW_FVF_POSITION_MASK 0x400Eu
#define DW_FVF_XYZRHW 0x0004u

typedef struct {
    int effects_before_ui; /* 0 or 1 */
    int reflection_scale;  /* 1, 2 or 4 */
} dw_config;

/* effects_before_ui = 0, reflection_scale = 1. */
dw_config dw_default(void);

/* Applies daoc-world.ini text (`key = value` lines; `;`/`#` comments, `[section]` lines and other keys ignored) over
 * *c. A value outside its key's set (effects_before_ui 0|1, reflection_scale 1|2|4) leaves the field as it was.
 * Returns how many lines were rejected; the first one is described in warn. */
int dw_parse(const char *text, size_t len, dw_config *c, char *warn, size_t warnlen);

/* 1 when any word lies in [DW_WATER_CALL_LO, DW_WATER_CALL_HI): the stack holds the reflection RT's call site. */
int dw_stack_is_water_rt(const uint32_t *words, int n);

/* The size a 512 reflection target is made: 512 * scale, capped at DW_REFLECTION_MAX. Any other size, or a scale of 1
 * or less, is returned unchanged. */
uint32_t dw_scaled_size(uint32_t native, int scale);

/* What a resource description is to the reflection rule: 'R' a 512x512 2D render target, 'D' a 512x512 depth-stencil
 * (surface or 2D texture), 0 anything else. */
char dw_reflection_kind(uint32_t type, uint32_t usage, uint32_t width, uint32_t height);

/* The 3D/UI boundary: the first draw of a frame to the swap chain's back buffer that is orthographic, after at least
 * one perspective back-buffer draw in the same frame. */
struct dw_frame {
    int saw_world; /* a perspective back-buffer draw happened this frame */
    int fired;     /* the effects ran this frame */
};

/* 1 when a draw is orthographic: the projection's _34 == 0 and _44 == 1, or the FVF's position is XYZRHW
 * (pre-transformed vertices, which skip the projection). */
int dw_ortho_or_rhw(float m34, float m44, uint32_t fvf);

/* Feeds one draw into the frame: a perspective draw to the back buffer sets saw_world; the first orthographic draw to
 * the back buffer after it sets fired and returns 1 (once per frame). Draws to other render targets change nothing. */
int dw_should_fire(struct dw_frame *f, int rt0_is_backbuffer, int ortho_or_rhw);

/* Starts the next frame (on present). */
void dw_frame_reset(struct dw_frame *f);

#endif
