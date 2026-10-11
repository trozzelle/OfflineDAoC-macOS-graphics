#include "sampler.h"

void gfx_stage_reset(gfx_stage *st)
{
    st->min = GFX_TEXF_POINT;
    st->mip = GFX_TEXF_NONE;
    st->maxaniso = 1;
    st->levels = 0;
    st->sent_min = GFX_TEXF_POINT;
    st->sent_maxaniso = 1;
    st->game_aniso = 0;
}

int gfx_forces(const gfx_stage *st)
{
    int linear = st->min == GFX_TEXF_LINEAR || st->min == GFX_TEXF_ANISOTROPIC;
    int mipmapped = st->mip == GFX_TEXF_POINT || st->mip == GFX_TEXF_LINEAR;
    return linear && mipmapped && st->levels > 1;
}

void gfx_set(gfx_stage *st, uint32_t type, uint32_t value)
{
    switch (type) {
    case GFX_SAMP_MINFILTER:
        st->min = value;
        if (value == GFX_TEXF_ANISOTROPIC)
            st->game_aniso = 1;
        break;
    case GFX_SAMP_MIPFILTER:
        st->mip = value;
        break;
    case GFX_SAMP_MAXANISOTROPY:
        st->maxaniso = value;
        break;
    default:
        break;
    }
}

int gfx_reconcile(gfx_stage *st, uint32_t level, uint32_t *min, uint32_t *maxaniso)
{
    int writes = 0;
    if (gfx_forces(st)) {
        *min = GFX_TEXF_ANISOTROPIC;
        *maxaniso = st->maxaniso > level ? st->maxaniso : level;
    } else {
        *min = st->min;
        *maxaniso = st->maxaniso;
    }
    if (*min != st->sent_min) {
        st->sent_min = *min;
        writes |= GFX_WRITE_MIN;
    }
    if (*maxaniso != st->sent_maxaniso) {
        st->sent_maxaniso = *maxaniso;
        writes |= GFX_WRITE_MAXANISO;
    }
    return writes;
}

void gfx_resync(gfx_stage *st, uint32_t dev_min, uint32_t dev_mip, uint32_t dev_maxaniso,
                uint32_t levels)
{
    if (dev_min == GFX_TEXF_ANISOTROPIC && !st->game_aniso) {
        st->min = GFX_TEXF_LINEAR;
    } else {
        st->min = dev_min;
        st->maxaniso = dev_maxaniso;
    }
    st->mip = dev_mip;
    st->levels = levels;
    st->sent_min = dev_min;
    st->sent_maxaniso = dev_maxaniso;
}
