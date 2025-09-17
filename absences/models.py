from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from datetime import timedelta
from .utils import est_jour_ouvre, compter_jours_ouvres
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.utils import timezone

# -----------------------------
# Constantes globales
# -----------------------------

ROLES = (
    ('admin', 'Admin'),
    ('drh', 'Directrice RH'),
    ('dp', 'Directeur Pays'),
    ('superieur', 'Supérieur hiérarchique'),
    ('collaborateur', 'Collaborateur'),    
)
# The `STATUT_ABSENCE` constant is a tuple of tuples that defines different status options for an
# absence in the system. Each tuple consists of two elements: the key representing the status and the
# corresponding display value. This constant is used as choices for the `statut` field in the
# `Absence` model to restrict the possible values for the status of an absence.

STATUT_ABSENCE = (
    ('en_attente', 'En attente'),
    ('approuve_superieur', 'Approuvé par supérieur'),
    ('verifie_drh', 'Vérifié par DRH'),
    ('valide_dp', 'Validé par DP'),
    ('rejete', 'Rejeté'),
    ('annulee', 'Annulée par le collaborateur'),
)

# -----------------------------
# Modèle : Type d'absence
# -----------------------------

class TypeAbsence(models.Model):
    nom = models.CharField(max_length=50)
    couleur = models.CharField(max_length=7, help_text="Code couleur hexadécimal (ex: #FF5733)")

    def __str__(self):
        return self.nom

# -----------------------------
# Modèle : Jours fériés
# -----------------------------

class JourFerie(models.Model):
    date = models.DateField(unique=True)
    description = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.date} - {self.description}"

# -----------------------------
# Modèle : Année et Mois
# -----------------------------

class Annee(models.Model):
    annee = models.IntegerField(unique=True)

    def __str__(self):
        return str(self.annee)

class Mois(models.Model):
    nom = models.CharField(max_length=20)
    numero = models.IntegerField()  # 1 = Janvier, 12 = Décembre

    def __str__(self):
        return self.nom

# -----------------------------
# Modèle : Profil utilisateur
# -----------------------------

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLES)
    superieur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="subordonnes")
    actif = models.BooleanField(default=True)
    poste = models.CharField(max_length=100, blank=True, null=True)
    doit_changer_mdp = models.BooleanField(default=True)
    

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_role_display()}"

# -----------------------------
# Modèle : Quotas par type d'absence et par année
# -----------------------------

class QuotaAbsence(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quotas')
    type_absence = models.ForeignKey(TypeAbsence, on_delete=models.CASCADE)
    annee = models.IntegerField()
    jours_disponibles = models.DecimalField(
    max_digits=5,      # ex: 999.99 max
    decimal_places=2,  # nombre de décimales
    default=Decimal("0.00"),
    validators=[MinValueValidator(Decimal("0.00"))]
)

    class Meta:
        unique_together = ('user', 'type_absence', 'annee')

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.type_absence.nom} {self.annee} ({self.jours_disponibles} jrs)"

# -----------------------------
# Modèle : Historique des validations
# -----------------------------

class ValidationHistorique(models.Model):
    absence = models.ForeignKey('Absence', on_delete=models.CASCADE, related_name='historiques')
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    role_valide = models.CharField(max_length=255,null=True)  # admin, drh, dp, superieur
    decision = models.CharField(max_length=255,null=True, blank=True, default='')     # valider, rejeter
    motif = models.TextField(blank=True, null=True)
    date_validation = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.absence} - {self.decision} par {self.utilisateur}"
# -----------------------------


# -----------------------------
# Modèle : Absence
# -----------------------------

class Absence(models.Model):
    STATUTS = STATUT_ABSENCE
    collaborateur = models.ForeignKey(User, on_delete=models.CASCADE, related_name='absences')
    type_absence = models.ForeignKey(TypeAbsence, on_delete=models.CASCADE)
    date_debut = models.DateField()
    date_fin = models.DateField(blank=True, null=True)
    nombre_jours = models.DecimalField(
        max_digits=5, decimal_places=1,
        help_text="Nombre total de jours ouvrés demandés (peut inclure des demi-journées)"
    )
    raison = models.TextField(blank=True, null=True)

    approuve_par_superieur = models.BooleanField(default=False)
    date_approbation_superieur = models.DateField(blank=True, null=True)

    verifie_par_drh = models.BooleanField(default=False)
    date_verification_drh = models.DateField(blank=True, null=True)

    valide_par_dp = models.BooleanField(default=False)
    date_validation_dp = models.DateField(blank=True, null=True)

    statut = models.CharField(
        max_length=30,
        choices=STATUT_ABSENCE,   # <-- utiliser la constante centrale
        default='en_attente'
    )
    

    justificatif = models.FileField(upload_to='justificatifs/', blank=True, null=True)
    annulee_par_collaborateur = models.BooleanField(default=False)
    motif_annulation = models.TextField(blank=True, null=True)


    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    
    def dernier_motif_rejet(self):
        dernier_rejet = self.historiques.filter(decision='rejeter').order_by('-date_validation').first()
        if dernier_rejet and dernier_rejet.motif:
            return dernier_rejet.motif
        return None

    def date_rejet(self):
        dernier_rejet = self.historiques.filter(decision='rejeter').order_by('-date_validation').first()
        if dernier_rejet:
            return dernier_rejet.date_validation
        return None

    def a_annulation(self):
        return self.annulee_par_collaborateur and bool(self.motif_annulation)

    def __str__(self):
        return f"{self.collaborateur.get_full_name()} - {self.type_absence.nom} du {self.date_debut} ({self.nombre_jours} jours ouvrés)"

    def duree(self):
        return self.nombre_jours

    def clean(self):
        if self.date_debut and self.nombre_jours:
            jours_restants = self.nombre_jours
            date_courante = self.date_debut
            while jours_restants > 0:
                if est_jour_ouvre(date_courante):
                    jours_restants -= 1
                date_courante += timedelta(days=1)
            self.date_fin = date_courante - timedelta(days=1)

        # Vérifie chevauchement
        if self.date_debut and self.date_fin:
            chevauchement = Absence.objects.filter(
                collaborateur=self.collaborateur,
                date_debut__lte=self.date_fin,
                date_fin__gte=self.date_debut
            ).exclude(id=self.id).exists()
            if chevauchement:
                raise ValidationError("Une autre absence chevauche déjà cette période.")

        # Vérifie quota
        annee = self.date_debut.year
        try:
            from .models import QuotaAbsence
            quota = QuotaAbsence.objects.get(user=self.collaborateur, type_absence=self.type_absence, annee=annee)
            if self.nombre_jours > quota.jours_disponibles:
                raise ValidationError(f"Quota insuffisant : {quota.jours_disponibles} jour(s) restants.")
        except QuotaAbsence.DoesNotExist:
            raise ValidationError("Aucun quota défini pour ce type d'absence pour cette année.")

    def save(self, *args, **kwargs):
        from .models import QuotaAbsence, ValidationHistorique
        if self.date_debut and self.nombre_jours:
            jours_restants = self.nombre_jours
            date_courante = self.date_debut
            while jours_restants > 0:
                if est_jour_ouvre(date_courante):
                    jours_restants -= 1
                date_courante += timedelta(days=1)
            self.date_fin = date_courante - timedelta(days=1)

        # Historique et déduction de quota
        if self.valide_par_dp:
            ValidationHistorique.objects.get_or_create(
                absence=self,
                utilisateur=self.collaborateur,
                role_valide='dp',
                decision='valider'
            )
            annee = self.date_debut.year
            try:
                quota = QuotaAbsence.objects.get(user=self.collaborateur, type_absence=self.type_absence, annee=annee)
                if quota.jours_disponibles >= self.nombre_jours:
                    quota.jours_disponibles -= self.nombre_jours
                    quota.save()
            except QuotaAbsence.DoesNotExist:
                pass

        super().save(*args, **kwargs)
        
        

class Recuperation(models.Model):
    utilisateur = models.ForeignKey(User, on_delete=models.CASCADE)
    motif = models.TextField()
    justificatif = models.FileField(upload_to='justificatifs_recuperations/')
    date_debut = models.DateField(default=timezone.now)  # nouvelle colonne
    nombre_jours = models.DecimalField(  # nouvelle colonne
        max_digits=5,
        decimal_places=1,
        default=1,
        help_text="Nombre total de jours de récupération (ex: 1, 1.5, 2)"
    )
    statut = models.CharField(max_length=30, default='en_attente')  # 'en_attente', 'valide', 'annulee'
    motif_annulation = models.TextField(blank=True, null=True)
    date_fin = models.DateField(blank=True, null=True)  # pour calculer la fin

    date_soumission = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"Récupération - {self.utilisateur.get_full_name()} "
            f"du {self.date_debut} ({self.nombre_jours} jours)"
        )
