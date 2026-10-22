#include <stdio.h>
int main(void) {
    static int a[200000];
    for (int i = 0; i < 200000; i++)
        a[i] = (i * 37 + 11) % 100003;

    for (int i = 1; i < 200000; i++) {
        int key = a[i];
        int j = i - 1;
        while (j >= 0 && a[j] > key) {
            a[j + 1] = a[j];
            j--;
        }
        a[j + 1] = key;
    }
    printf("%d %d\n", a[0], a[199999]);
    return 0;
}
