#include <stdio.h>
#define N 60

int main(void) {
    static double a[N][N], b[N][N], c[N][N];

    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            a[i][j] = i + j + 1;
            b[i][j] = i - j + 2;
        }

    for (int repeat = 0; repeat < 100; repeat++)
        for (int i = 0; i < N; i++)
            for (int j = 0; j < N; j++)
                for (int k = 0; k < N; k++)
                    c[i][j] += a[i][k] * b[k][j];

    printf("%.2f\n", c[N - 1][N - 1]);
    return 0;
}
