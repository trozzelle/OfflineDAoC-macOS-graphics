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

/* A frame's draws, in order: each is "rt0 on the back buffer, orthographic or XYZRHW". */
#define WORLD 1, 0     /* perspective draw to the back buffer */
#define UI 1, 1        /* orthographic draw to the back buffer */
#define SHADOW 0, 1    /* the shadow RT's draws: orthographic, offscreen */
#define REFLECT 0, 0   /* the reflection RT's draws: perspective, offscreen */

static void boundary_fires_at_first_ui_draw(void)
{
    struct dw_frame f = {0, 0};
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(f.saw_world == 1 && f.fired == 0);
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(dw_should_fire(&f, UI) == 1);
    EXPECT(f.fired == 1);
}

static void no_boundary_without_perspective_draw(void)
{
    struct dw_frame f = {0, 0};
    /* Login and character-select style frames: only UI draws; the effects stay at Present. */
    EXPECT(dw_should_fire(&f, UI) == 0);
    EXPECT(dw_should_fire(&f, UI) == 0);
    EXPECT(f.saw_world == 0 && f.fired == 0);
    /* A UI draw before the world does not count; the first one after it does. */
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(dw_should_fire(&f, UI) == 1);
}

static void boundary_fires_once_per_frame(void)
{
    struct dw_frame f = {0, 0};
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(dw_should_fire(&f, UI) == 1);
    EXPECT(dw_should_fire(&f, UI) == 0);
    EXPECT(dw_should_fire(&f, WORLD) == 0); /* a 3D model in a UI window */
    EXPECT(dw_should_fire(&f, UI) == 0);
    EXPECT(f.fired == 1);
}

static void offscreen_draws_never_fire(void)
{
    struct dw_frame f = {0, 0};
    EXPECT(dw_should_fire(&f, SHADOW) == 0);
    EXPECT(dw_should_fire(&f, REFLECT) == 0);
    EXPECT(f.saw_world == 0); /* offscreen perspective draws are not the world on the back buffer */
    EXPECT(dw_should_fire(&f, UI) == 0);
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(dw_should_fire(&f, SHADOW) == 0);  /* after the world: still offscreen */
    EXPECT(dw_should_fire(&f, REFLECT) == 0);
    EXPECT(f.fired == 0);
    EXPECT(dw_should_fire(&f, UI) == 1);
}

static void reset_on_present(void)
{
    struct dw_frame f = {0, 0};
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(dw_should_fire(&f, UI) == 1);
    dw_frame_reset(&f);
    EXPECT(f.saw_world == 0 && f.fired == 0);
    EXPECT(dw_should_fire(&f, UI) == 0); /* the world of the last frame does not carry over */
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    EXPECT(dw_should_fire(&f, UI) == 1);
    /* A frame that drew the world but no UI: the next frame starts clean. */
    dw_frame_reset(&f);
    EXPECT(dw_should_fire(&f, WORLD) == 0);
    dw_frame_reset(&f);
    EXPECT(dw_should_fire(&f, UI) == 0);
}

static void ortho_or_rhw_rule(void)
{
    const uint32_t xyz_tex = 0x002u | 0x100u, rhw_tex = DW_FVF_XYZRHW | 0x040u | 0x100u;
    EXPECT(dw_ortho_or_rhw(0.0f, 1.0f, xyz_tex));   /* _34 = 0, _44 = 1 */
    EXPECT(!dw_ortho_or_rhw(1.0f, 0.0f, xyz_tex));  /* a perspective projection */
    EXPECT(!dw_ortho_or_rhw(0.0f, 0.5f, xyz_tex));
    EXPECT(!dw_ortho_or_rhw(1.0f, 1.0f, xyz_tex));
    EXPECT(dw_ortho_or_rhw(1.0f, 0.0f, rhw_tex));   /* pre-transformed vertices ignore the projection */
    EXPECT(!dw_ortho_or_rhw(1.0f, 0.0f, 0x4002u)); /* XYZW, not XYZRHW */
    EXPECT(!dw_ortho_or_rhw(1.0f, 0.0f, 0));       /* a vertex declaration (no FVF) */
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
    EXPECT(c.reflection_scale == 2);
}

int main(void)
{
    stack_matches_water_call_site();
    stack_rejects_shadow_call_sites();
    scaled_size_only_512();
    reflection_kind_rt_and_depth();
    ini_parses_defaults();
    shipped_ini_parses();
    boundary_fires_at_first_ui_draw();
    no_boundary_without_perspective_draw();
    boundary_fires_once_per_frame();
    offscreen_draws_never_fire();
    reset_on_present();
    ortho_or_rhw_rule();
    if (failures)
        printf("%d failure(s)\n", failures);
    else
        printf("test_world: all passed\n");
    return failures != 0;
}
