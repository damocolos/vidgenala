# 3-beat test fixture code for Nalarka Studio

def beat_01(v):
    v.backdrop_circle(r=200, color="yellow_stage")
    v.rect(0, 0, 180, 100, radius=20, color="dark")
    v.circle(0, 0, 30, color="red_hot")


def beat_02(v):
    v.circle(0, -50, 80, color="blue_soft")
    v.wave_line(-150, 50, 150, 50, color="dark", width=8)


def beat_03(v):
    v.backdrop_circle(r=220, color="mint_soft")
    v.waveform_bars(0, 0, w=300, h=100, count=9, color="dark")
