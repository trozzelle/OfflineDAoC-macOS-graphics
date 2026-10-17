/* daoc-world add-on rules (see world_core.h). */
#include "world_core.h"

#include <stdio.h>
#include <string.h>

dw_config dw_default(void)
{
    dw_config c = {0, 1};
    return c;
}

static int blank(char ch)
{
    return ch == ' ' || ch == '\t' || ch == '\r';
}

/* The whole of [s, e) as a decimal number of at most 3 digits; -1 when it is not one. */
static int number(const char *s, const char *e)
{
    int v = 0, digits = 0;
    for (; s < e; s++) {
        if (*s < '0' || *s > '9' || ++digits > 3)
            return -1;
        v = v * 10 + (*s - '0');
    }
    return digits ? v : -1;
}

static int key_is(const char *k, size_t n, const char *name)
{
    return strlen(name) == n && memcmp(k, name, n) == 0;
}

int dw_parse(const char *text, size_t len, dw_config *c, char *warn, size_t warnlen)
{
    size_t i = 0;
    int rejected = 0;
    while (i < len) {
        size_t end = i;
        const char *k, *ke, *v, *ve;
        int value, ok = 1;
        while (end < len && text[end] != '\n')
            end++;
        k = text + i, ve = text + end;
        i = end + 1;
        while (k < ve && blank(*k))
            k++;
        if (k == ve || *k == ';' || *k == '#' || *k == '[')
            continue;
        for (v = k; v < ve && *v != '='; v++)
            ;
        if (v == ve)
            continue;
        for (ke = v; ke > k && blank(ke[-1]); ke--)
            ;
        for (v++; v < ve && blank(*v); v++)
            ;
        while (ve > v && blank(ve[-1]))
            ve--;
        value = number(v, ve);
        if (key_is(k, (size_t)(ke - k), "effects_before_ui")) {
            if (value == 0 || value == 1)
                c->effects_before_ui = value;
            else
                ok = 0;
        } else if (key_is(k, (size_t)(ke - k), "reflection_scale")) {
            if (value == 1 || value == 2 || value == 4)
                c->reflection_scale = value;
            else
                ok = 0;
        }
        if (!ok && rejected++ == 0 && warnlen) {
            snprintf(warn, warnlen, "bad value for %.*s: '%.*s'", (int)(ke - k), k, (int)(ve - v), v);
            warn[warnlen - 1] = 0; /* an msvcrt snprintf leaves a truncated result unterminated */
        }
    }
    return rejected;
}

int dw_stack_is_water_rt(const uint32_t *words, int n)
{
    for (int i = 0; i < n; i++)
        if (words[i] >= DW_WATER_CALL_LO && words[i] < DW_WATER_CALL_HI)
            return 1;
    return 0;
}

uint32_t dw_scaled_size(uint32_t native, int scale)
{
    uint32_t s;
    if (native != DW_REFLECTION_NATIVE || scale <= 1)
        return native;
    s = native * (uint32_t)scale;
    return s > DW_REFLECTION_MAX ? DW_REFLECTION_MAX : s;
}

char dw_reflection_kind(uint32_t type, uint32_t usage, uint32_t width, uint32_t height)
{
    if (width != DW_REFLECTION_NATIVE || height != DW_REFLECTION_NATIVE)
        return 0;
    if (usage & DW_USAGE_DEPTH_STENCIL)
        return type == DW_TYPE_SURFACE || type == DW_TYPE_TEXTURE_2D ? 'D' : 0;
    if (usage & DW_USAGE_RENDER_TARGET)
        return type == DW_TYPE_TEXTURE_2D ? 'R' : 0;
    return 0;
}
