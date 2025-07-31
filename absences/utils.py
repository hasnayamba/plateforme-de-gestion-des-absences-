from datetime import timedelta
from .models import *

def est_jour_ouvre(date_test):
    """
    Retourne True si la date est un jour ouvré (lundi à vendredi, non férié)
    """
    from .models import JourFerie
    if date_test.weekday() >= 5:  # samedi (5) ou dimanche (6)
        return False
    return not JourFerie.objects.filter(date=date_test).exists()

def compter_jours_ouvres(date_debut, date_fin):
    """
    Calcule le nombre de jours ouvrés (hors week-ends et jours fériés)
    entre `date_debut` et `date_fin` inclus.
    """
    if not date_debut or not date_fin:
        return 0

    jours_ouvres = 0
    date_courante = date_debut

    while date_courante <= date_fin:
        if est_jour_ouvre(date_courante):
            jours_ouvres += 1
        date_courante += timedelta(days=1)

    return jours_ouvres
