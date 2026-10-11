/* Host build of proxy/sampler.c: the forcing rule and its state transitions.
 * Built and run by test_sampler.py; prints one line per failure. */
#include <stdio.h>

#include "../proxy/sampler.h"

static int failures;

#define EXPECT(cond)                                                     \
    do {                                                                 \
        if (!(cond)) {                                                   \
            printf("%s:%d: %s\n", __FILE__, __LINE__, #cond);            \
            failures++;                                                  \
        }                                                                \
    } while (0)

/* What the device holds after reconciling. */
static void settle(gfx_stage *st)
{
    uint32_t min, maxaniso;
    gfx_reconcile(st, 16, &min, &maxaniso);
}

/* A stage the game set to trilinear with a 9-level texture bound. */
static gfx_stage trilinear(void)
{
    gfx_stage st;
    gfx_stage_reset(&st);
    gfx_set(&st, GFX_SAMP_MINFILTER, GFX_TEXF_LINEAR);
    gfx_set(&st, GFX_SAMP_MIPFILTER, GFX_TEXF_LINEAR);
    st.levels = 9;
    return st;
}

static void defaults_are_not_forced(void)
{
    gfx_stage st;
    uint32_t min, maxaniso;
    gfx_stage_reset(&st);
    st.levels = 9;
    EXPECT(!gfx_forces(&st));
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == 0);
}

static void trilinear_mipmapped_is_forced_and_written_once(void)
{
    gfx_stage st = trilinear();
    uint32_t min, maxaniso;
    EXPECT(gfx_forces(&st));
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == (GFX_WRITE_MIN | GFX_WRITE_MAXANISO));
    EXPECT(min == GFX_TEXF_ANISOTROPIC && maxaniso == 16);
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == 0);
    /* The game still sees its own values. */
    EXPECT(st.min == GFX_TEXF_LINEAR && st.maxaniso == 1);
}

static void point_mips_are_forced_too(void)
{
    gfx_stage st = trilinear();
    gfx_set(&st, GFX_SAMP_MIPFILTER, GFX_TEXF_POINT);
    EXPECT(gfx_forces(&st));
}

static void no_mips_point_min_or_one_level_are_left_alone(void)
{
    gfx_stage st = trilinear();
    gfx_set(&st, GFX_SAMP_MIPFILTER, GFX_TEXF_NONE);
    EXPECT(!gfx_forces(&st));

    st = trilinear();
    gfx_set(&st, GFX_SAMP_MINFILTER, GFX_TEXF_POINT);
    EXPECT(!gfx_forces(&st));

    st = trilinear();
    st.levels = 1;
    EXPECT(!gfx_forces(&st));

    st = trilinear();
    st.levels = 0; /* no texture bound */
    EXPECT(!gfx_forces(&st));
}

static void leaving_the_rule_restores_the_game_values(void)
{
    gfx_stage st = trilinear();
    uint32_t min, maxaniso;
    settle(&st);
    gfx_set(&st, GFX_SAMP_MIPFILTER, GFX_TEXF_NONE);
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == (GFX_WRITE_MIN | GFX_WRITE_MAXANISO));
    EXPECT(min == GFX_TEXF_LINEAR && maxaniso == 1);
}

static void a_higher_game_maxanisotropy_is_kept(void)
{
    gfx_stage st = trilinear();
    uint32_t min, maxaniso;
    gfx_set(&st, GFX_SAMP_MAXANISOTROPY, 32);
    gfx_reconcile(&st, 16, &min, &maxaniso);
    EXPECT(maxaniso == 32);
}

static void max_anisotropy_written_while_forced_waits_for_the_rule(void)
{
    gfx_stage st = trilinear();
    uint32_t min, maxaniso;
    settle(&st);
    gfx_set(&st, GFX_SAMP_MAXANISOTROPY, 4);
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == 0);
    gfx_set(&st, GFX_SAMP_MIPFILTER, GFX_TEXF_NONE);
    gfx_reconcile(&st, 16, &min, &maxaniso);
    EXPECT(maxaniso == 4 && st.maxaniso == 4);
}

static void the_game_asking_for_anisotropy_itself_is_passed_on(void)
{
    gfx_stage st;
    uint32_t min, maxaniso;
    gfx_stage_reset(&st);
    gfx_set(&st, GFX_SAMP_MINFILTER, GFX_TEXF_ANISOTROPIC);
    gfx_set(&st, GFX_SAMP_MAXANISOTROPY, 8);
    EXPECT(st.game_aniso);
    /* No mips: not forced, but the game's own request reaches the device. */
    gfx_reconcile(&st, 16, &min, &maxaniso);
    EXPECT(min == GFX_TEXF_ANISOTROPIC && maxaniso == 8);
}

static void resync_reads_forcing_back_as_the_game_linear(void)
{
    gfx_stage st = trilinear();
    uint32_t min, maxaniso;
    settle(&st);
    /* A state block put back the forced values this proxy had sent. */
    gfx_resync(&st, GFX_TEXF_ANISOTROPIC, GFX_TEXF_LINEAR, 16, 9);
    EXPECT(st.min == GFX_TEXF_LINEAR && st.maxaniso == 1 && st.mip == GFX_TEXF_LINEAR);
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == 0);
}

static void resync_to_unforced_values_forces_again(void)
{
    gfx_stage st = trilinear();
    uint32_t min, maxaniso;
    settle(&st);
    /* A block recorded the game's raw values (recording passes them through). */
    gfx_resync(&st, GFX_TEXF_LINEAR, GFX_TEXF_POINT, 1, 9);
    EXPECT(st.min == GFX_TEXF_LINEAR && st.mip == GFX_TEXF_POINT);
    EXPECT(gfx_reconcile(&st, 16, &min, &maxaniso) == (GFX_WRITE_MIN | GFX_WRITE_MAXANISO));
    EXPECT(min == GFX_TEXF_ANISOTROPIC && maxaniso == 16);
}

static void resync_keeps_the_game_anisotropy_once_it_asked(void)
{
    gfx_stage st;
    gfx_stage_reset(&st);
    gfx_set(&st, GFX_SAMP_MINFILTER, GFX_TEXF_ANISOTROPIC);
    gfx_resync(&st, GFX_TEXF_ANISOTROPIC, GFX_TEXF_NONE, 8, 1);
    EXPECT(st.min == GFX_TEXF_ANISOTROPIC && st.maxaniso == 8);
}

static void reset_forgets_everything(void)
{
    gfx_stage st = trilinear();
    settle(&st);
    gfx_stage_reset(&st);
    EXPECT(st.min == GFX_TEXF_POINT && st.mip == GFX_TEXF_NONE && st.maxaniso == 1);
    EXPECT(st.sent_min == GFX_TEXF_POINT && st.sent_maxaniso == 1 && st.levels == 0);
}

#define PARSE(text) gfx_parse_level(text, sizeof(text) - 1)

static void level_lines_are_parsed(void)
{
    EXPECT(PARSE("anisotropy = 8\n") == 8);
    EXPECT(PARSE("# Written by daoc-gfx\r\n\r\n  anisotropy=4\r\n") == 4);
    EXPECT(PARSE("other = 2\nanisotropy = 16") == 16);
    EXPECT(PARSE("anisotropy = 2\nanisotropy = 8\n") == 2); /* the first one counts */
}

static void bad_or_missing_levels_are_rejected(void)
{
    EXPECT(PARSE("") == 0);
    EXPECT(PARSE("# anisotropy = 8\n") == 0);
    EXPECT(PARSE("anisotropy = 0\n") == 0);
    EXPECT(PARSE("anisotropy = 17\n") == 0);
    EXPECT(PARSE("anisotropy = 8x\n") == 0);
    EXPECT(PARSE("anisotropy = 0008\n") == 0);
    EXPECT(PARSE("anisotropy =\n") == 0);
    EXPECT(PARSE("anisotropy_level = 8\n") == 0);
}

int main(void)
{
    defaults_are_not_forced();
    trilinear_mipmapped_is_forced_and_written_once();
    point_mips_are_forced_too();
    no_mips_point_min_or_one_level_are_left_alone();
    leaving_the_rule_restores_the_game_values();
    a_higher_game_maxanisotropy_is_kept();
    max_anisotropy_written_while_forced_waits_for_the_rule();
    the_game_asking_for_anisotropy_itself_is_passed_on();
    resync_reads_forcing_back_as_the_game_linear();
    resync_to_unforced_values_forces_again();
    resync_keeps_the_game_anisotropy_once_it_asked();
    reset_forgets_everything();
    level_lines_are_parsed();
    bad_or_missing_levels_are_rejected();
    return failures ? 1 : 0;
}
