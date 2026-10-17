/* Host build of world_core.c (make -C addon test): prints one line per failure, exits non-zero on any. */
#include <stdio.h>
#include <string.h>

#include "world_core.h"

static int failures;

#define EXPECT(cond)                                                     \
    do {                                                                 \
        if (!(cond)) {                                                   \
            printf("%s:%d: %s\n", __FILE__, __LINE__, #cond);            \
            failures++;                                                  \
        }                                                                \
    } while (0)

static void stack_matches_water_call_site(void)
{
    const uint32_t direct[] = {0x56a930u};
    const uint32_t deeper[] = {0x401234u, 0x5a0000u, 0x56a930u, 0x45f000u};
    const uint32_t edges[] = {0x56a900u};
    const uint32_t last[] = {0x56a95fu};
    EXPECT(dw_stack_is_water_rt(direct, 1));
    EXPECT(dw_stack_is_water_rt(deeper, 4));
    EXPECT(dw_stack_is_water_rt(edges, 1));
    EXPECT(dw_stack_is_water_rt(last, 1));
    EXPECT(!dw_stack_is_water_rt(deeper, 2)); /* only the first n words count */
    EXPECT(!dw_stack_is_water_rt(direct, 0));
}

static void stack_rejects_shadow_call_sites(void)
{
    const uint32_t shadow[] = {0x574853u, 0x5730deu, 0x573062u};
    const uint32_t around[] = {0x56a8ffu, 0x56a960u};
    EXPECT(!dw_stack_is_water_rt(shadow, 3));
    EXPECT(!dw_stack_is_water_rt(around, 2));
}

static void scaled_size_only_512(void)
{
    EXPECT(dw_scaled_size(512, 1) == 512);
    EXPECT(dw_scaled_size(512, 2) == 1024);
    EXPECT(dw_scaled_size(512, 4) == 2048);
    EXPECT(dw_scaled_size(512, 8) == 2048); /* capped */
    EXPECT(dw_scaled_size(512, 0) == 512);
    EXPECT(dw_scaled_size(512, -2) == 512);
    EXPECT(dw_scaled_size(256, 2) == 256);
    EXPECT(dw_scaled_size(1024, 2) == 1024);
    EXPECT(dw_scaled_size(1024, 4) == 1024);
}

static void reflection_kind_rt_and_depth(void)
{
    EXPECT(dw_reflection_kind(DW_TYPE_TEXTURE_2D, DW_USAGE_RENDER_TARGET | 0xC0u, 512, 512) == 'R');
    EXPECT(dw_reflection_kind(DW_TYPE_SURFACE, DW_USAGE_DEPTH_STENCIL | 0x800u, 512, 512) == 'D');
    EXPECT(dw_reflection_kind(DW_TYPE_TEXTURE_2D, DW_USAGE_DEPTH_STENCIL | 0xC0u, 512, 512) == 'D');
    EXPECT(dw_reflection_kind(DW_TYPE_TEXTURE_2D, 0xC0u, 512, 512) == 0);   /* plain texture */
    EXPECT(dw_reflection_kind(DW_TYPE_SURFACE, DW_USAGE_RENDER_TARGET, 512, 512) == 0);
    EXPECT(dw_reflection_kind(DW_TYPE_TEXTURE_2D, DW_USAGE_RENDER_TARGET, 512, 256) == 0);
    EXPECT(dw_reflection_kind(DW_TYPE_TEXTURE_2D, DW_USAGE_RENDER_TARGET, 1024, 1024) == 0);
    EXPECT(dw_reflection_kind(DW_TYPE_SURFACE, DW_USAGE_DEPTH_STENCIL, 1024, 768) == 0);
}

static dw_config parsed(const char *text, int *rejected)
{
    dw_config c = dw_default();
    char warn[128] = "";
    *rejected = dw_parse(text, strlen(text), &c, warn, sizeof(warn));
    if (*rejected)
        EXPECT(warn[0] != 0);
    return c;
}

static void ini_parses_defaults(void)
{
    dw_config c = dw_default();
    int bad;
    EXPECT(c.effects_before_ui == 0 && c.reflection_scale == 1);

    c = parsed("", &bad);
    EXPECT(bad == 0 && c.effects_before_ui == 0 && c.reflection_scale == 1);

    c = parsed("; daoc-world\r\n[world]\r\neffects_before_ui = 1\r\nreflection_scale = 2\r\n", &bad);
    EXPECT(bad == 0 && c.effects_before_ui == 1 && c.reflection_scale == 2);

    c = parsed("reflection_scale=4\n# comment\nsomething_else = 7", &bad);
    EXPECT(bad == 0 && c.effects_before_ui == 0 && c.reflection_scale == 4);

    c = parsed("  reflection_scale\t=\t1  \n", &bad);
    EXPECT(bad == 0 && c.reflection_scale == 1);

    /* Values outside a key's set keep the default. */
    c = parsed("reflection_scale = 3\n", &bad);
    EXPECT(bad == 1 && c.reflection_scale == 1);
    c = parsed("reflection_scale = 0\nreflection_scale = 8\nreflection_scale = 2x\nreflection_scale =\n", &bad);
    EXPECT(bad == 4 && c.reflection_scale == 1);
    c = parsed("effects_before_ui = 2\neffects_before_ui = yes\n", &bad);
    EXPECT(bad == 2 && c.effects_before_ui == 0);
    c = parsed("effects_before_ui = 1\nreflection_scale = 16\n", &bad);
    EXPECT(bad == 1 && c.effects_before_ui == 1 && c.reflection_scale == 1);
}

static void shipped_ini_parses(void)
{
    char text[4096];
    size_t len;
    dw_config c = dw_default();
    char warn[128] = "";
    FILE *f = fopen("daoc-world.ini", "rb");
    EXPECT(f != NULL);
    if (!f)
        return;
    len = fread(text, 1, sizeof(text), f);
    fclose(f);
    EXPECT(dw_parse(text, len, &c, warn, sizeof(warn)) == 0);
    EXPECT(c.effects_before_ui == 1);
}

int main(void)
{
    stack_matches_water_call_site();
    stack_rejects_shadow_call_sites();
    scaled_size_only_512();
    reflection_kind_rt_and_depth();
    ini_parses_defaults();
    shipped_ini_parses();
    if (failures)
        printf("%d failure(s)\n", failures);
    else
        printf("test_world: all passed\n");
    return failures != 0;
}
