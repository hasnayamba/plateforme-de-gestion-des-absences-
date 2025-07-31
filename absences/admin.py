from django.contrib import admin
from .models import (
    TypeAbsence, JourFerie, Annee, Mois, Profile,
    QuotaAbsence, Absence, ValidationHistorique
)

# -----------------------------
# Type d'absence
# -----------------------------
@admin.register(TypeAbsence)
class TypeAbsenceAdmin(admin.ModelAdmin):
    list_display = ('nom', 'couleur')
    search_fields = ('nom',)
    ordering = ('nom',)

# -----------------------------
# Jour férié
# -----------------------------
@admin.register(JourFerie)
class JourFerieAdmin(admin.ModelAdmin):
    list_display = ('date', 'description')
    search_fields = ('description',)
    ordering = ('date',)

# -----------------------------
# Annee
# -----------------------------
@admin.register(Annee)
class AnneeAdmin(admin.ModelAdmin):
    list_display = ('annee',)
    ordering = ('annee',)

# -----------------------------
# Mois
# -----------------------------
@admin.register(Mois)
class MoisAdmin(admin.ModelAdmin):
    list_display = ('numero', 'nom')
    ordering = ('numero',)

# -----------------------------
# Profile utilisateur
# -----------------------------
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'superieur', 'actif')
    list_filter = ('role', 'actif')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ['user', 'superieur']

# -----------------------------
# Quota d'absence
# -----------------------------
@admin.register(QuotaAbsence)
class QuotaAbsenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'type_absence', 'annee', 'jours_disponibles')
    list_filter = ('type_absence', 'annee')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ['user', 'type_absence']

# -----------------------------
# Absence
# -----------------------------
@admin.register(Absence)
class AbsenceAdmin(admin.ModelAdmin):
    list_display = (
        'collaborateur', 'type_absence', 'date_debut', 'date_fin', 'nombre_jours',
        'statut', 'approuve_par_superieur', 'verifie_par_drh', 'valide_par_dp'
    )
    list_filter = ('type_absence', 'statut', 'approuve_par_superieur', 'verifie_par_drh', 'valide_par_dp')
    search_fields = ('collaborateur__username', 'collaborateur__first_name', 'collaborateur__last_name')
    readonly_fields = ('date_creation', 'date_modification', 'date_fin')
    autocomplete_fields = ['collaborateur', 'type_absence']

# -----------------------------
# Historique des validations
# -----------------------------
@admin.register(ValidationHistorique)
class ValidationHistoriqueAdmin(admin.ModelAdmin):
    list_display = ('absence', 'utilisateur', 'action', 'date_action')
    list_filter = ('action', 'date_action')
    search_fields = ('utilisateur__username', 'absence__collaborateur__username')
    autocomplete_fields = ['absence', 'utilisateur']
