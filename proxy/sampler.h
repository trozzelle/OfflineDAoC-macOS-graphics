/* Forced anisotropic filtering: the per-stage sampler bookkeeping.
 *
 * Plain C with no Windows headers, so the tests can build it for the host.
 * d3d9_proxy.c feeds it the game's SetSamplerState/SetTexture calls and sends
 * the device what gfx_reconcile asks for.
 *
 * mtld3d gives a sampler its D3DSAMP_MAXANISOTROPY only when a filter is
 * D3DTEXF_ANISOTROPIC, and DAoC never asks for that, so a forced stage is sent
 * MINFILTER = ANISOTROPIC and MAXANISOTROPY = the forced level. A stage is
 * forced under DXVK's rule (d3d9.samplerAnisotropy): the game filters it
 * linearly, uses mipmaps, and the bound texture has more than one level.
 * Bilinear-only passes (no mip filter, single-level render targets) keep the
 * game's filtering. The game always reads back its own values.
 */
#ifndef DAOC_GFX_SAMPLER_H
#define DAOC_GFX_SAMPLER_H

#include <stddef.h>
#include <stdint.h>

/* D3D9 values (d3d9types.h). */
enum {
    GFX_SAMP_MAGFILTER = 5,
    GFX_SAMP_MINFILTER = 6,
    GFX_SAMP_MIPFILTER = 7,
    GFX_SAMP_MAXANISOTROPY = 10,
};
enum {
    GFX_TEXF_NONE = 0,
    GFX_TEXF_POINT = 1,
    GFX_TEXF_LINEAR = 2,
    GFX_TEXF_ANISOTROPIC = 3,
};

/* Pixel samplers 0..15; the displacement and vertex samplers (256..260) are
 * left alone. */
#define GFX_STAGES 16

enum {
    GFX_WRITE_MIN = 1,
    GFX_WRITE_MAXANISO = 2,
};

typedef struct {
    /* What the game set (and reads back). */
    uint32_t min, mip, maxaniso;
    /* Mip levels of the bound texture; 0 when none is bound. */
    uint32_t levels;
    /* What the device holds for MINFILTER and MAXANISOTROPY. */
    uint32_t sent_min, sent_maxaniso;
    /* The game asked for ANISOTROPIC itself at least once. */
    int game_aniso;
} gfx_stage;

/* D3D9's defaults, which a device holds after CreateDevice and Reset. */
void gfx_stage_reset(gfx_stage *st);

/* Whether the stage gets forced anisotropy. */
int gfx_forces(const gfx_stage *st);

/* Record a game write of MINFILTER, MIPFILTER or MAXANISOTROPY. Other types
 * are ignored. */
void gfx_set(gfx_stage *st, uint32_t type, uint32_t value);

/* The MINFILTER and MAXANISOTROPY the device should hold at forced `level`.
 * Returns which of them differ from what it holds (GFX_WRITE_*), stores the
 * values to send in *min and *maxaniso, and records them as sent. */
int gfx_reconcile(gfx_stage *st, uint32_t level, uint32_t *min, uint32_t *maxaniso);

/* Rebuild the game's values from what the device holds, after something
 * outside SetSamplerState (a state block's Apply) changed it. A device value
 * of MINFILTER = ANISOTROPIC is taken as this proxy's forcing, so the game's
 * value becomes LINEAR and its MAXANISOTROPY is kept, unless the game has
 * asked for ANISOTROPIC itself. */
void gfx_resync(gfx_stage *st, uint32_t dev_min, uint32_t dev_mip, uint32_t dev_maxaniso,
                uint32_t levels);

/* The forced level: daoc-gfx-d3d9.conf next to the DLL sets it (daoc-gfx
 * writes 2, 4, 8 or 16), 16 without the file. mtld3d caps it at 16. */
#define GFX_LEVEL_DEFAULT 16
#define GFX_LEVEL_MAX 16

/* The value of the first `anisotropy = N` line of a daoc-gfx-d3d9.conf text,
 * N from 1 to GFX_LEVEL_MAX. Blank lines, `#` comments and other keys are
 * skipped. Returns 0 when there is no such line or its value is not a whole
 * number in range. */
uint32_t gfx_parse_level(const char *text, size_t len);

#endif
